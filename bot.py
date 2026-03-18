"""
Polymarket Signal Bot
---------------------
Fetches Polymarket markets, scores them for trade probability,
and sends top picks to your Telegram chat.
NO money is handled. YOU decide which trades to take.
"""

import os
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_IDS   = [int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x]

POLYMARKET_API     = "https://clob.polymarket.com"
GAMMA_API          = "https://gamma-api.polymarket.com"

TOP_N              = 10          # How many markets to surface per scan
MIN_LIQUIDITY      = 1_000       # Minimum $1k liquidity
MAX_PRICE_EDGE     = 0.15        # Ignore markets priced 0–5% or 95–100% (too certain)
MIN_VOLUME_24H     = 500         # Minimum 24-hour volume ($)

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# ── Polymarket helpers ───────────────────────────────────────────────────────

async def fetch_markets(session: aiohttp.ClientSession) -> list[dict]:
    """Pull active markets from Gamma (enriched metadata)."""
    url = f"{GAMMA_API}/markets"
    params = {
        "active": "true",
        "closed": "false",
        "limit": 200,
        "order": "volume24hr",
        "ascending": "false",
    }
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
        r.raise_for_status()
        data = await r.json()
    return data if isinstance(data, list) else data.get("markets", [])


def score_market(m: dict) -> float:
    """
    Higher score = better trade signal.

    Factors:
      • Price edge  – odds near 50% = maximum uncertainty = most opportunity
      • Liquidity   – more liquid = easier entry/exit
      • Volume 24h  – active markets have sharper prices
      • Time decay  – markets closing within 7 days get a boost (catalyst clarity)
    """
    try:
        # Best YES price (0–1 range)
        outcomes = m.get("outcomes", "[]")
        if isinstance(outcomes, str):
            import json
            outcomes = json.loads(outcomes)

        prices = []
        for o in outcomes:
            p = float(o.get("price", 0))
            if 0 < p < 1:
                prices.append(p)

        if not prices:
            return 0.0

        best = max(prices)

        # Skip near-certain markets
        if best > (1 - MAX_PRICE_EDGE) or best < MAX_PRICE_EDGE:
            return 0.0

        # Edge score: peaks at 0.5, falls toward 0 and 1
        edge_score = 1 - abs(best - 0.5) * 2   # 0 → 1

        liquidity  = float(m.get("liquidity", 0))
        volume_24h = float(m.get("volume24hr", 0))

        if liquidity < MIN_LIQUIDITY or volume_24h < MIN_VOLUME_24H:
            return 0.0

        liq_score = min(liquidity / 100_000, 1.0)
        vol_score = min(volume_24h / 50_000, 1.0)

        # Time decay bonus
        end_date = m.get("endDate") or m.get("end_date_iso")
        time_score = 0.0
        if end_date:
            try:
                deadline = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                days_left = (deadline - datetime.now(timezone.utc)).days
                if 0 < days_left <= 7:
                    time_score = 0.3
                elif days_left <= 30:
                    time_score = 0.1
            except Exception:
                pass

        return round(
            edge_score * 0.40
            + liq_score * 0.30
            + vol_score * 0.20
            + time_score * 0.10,
            4,
        )
    except Exception as e:
        log.debug("score_market error: %s", e)
        return 0.0


def format_market(m: dict, rank: int, score: float) -> str:
    """Return a clean Telegram message for one market."""
    title     = m.get("question") or m.get("title") or "Unknown"
    liquidity = float(m.get("liquidity", 0))
    vol_24h   = float(m.get("volume24hr", 0))
    slug      = m.get("slug") or m.get("conditionId") or ""
    url       = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"

    outcomes = m.get("outcomes", "[]")
    if isinstance(outcomes, str):
        import json
        outcomes = json.loads(outcomes)

    price_lines = []
    for o in outcomes:
        name  = o.get("name", "?")
        price = float(o.get("price", 0))
        pct   = round(price * 100, 1)
        bar   = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        price_lines.append(f"  {name}: {bar} {pct}¢")

    prices_str = "\n".join(price_lines) if price_lines else "  N/A"

    end_date = m.get("endDate") or m.get("end_date_iso") or "—"
    if end_date and end_date != "—":
        try:
            d = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            days_left = (d - datetime.now(timezone.utc)).days
            end_date = f"{d.strftime('%b %d, %Y')} ({days_left}d left)"
        except Exception:
            pass

    stars = "⭐" * min(5, max(1, int(score * 10)))

    return (
        f"*#{rank} — {title}*\n"
        f"\n{prices_str}\n"
        f"\n📊 Signal score: {stars} `{score:.2f}`"
        f"\n💧 Liquidity: ${liquidity:,.0f}"
        f"\n📈 Vol 24h: ${vol_24h:,.0f}"
        f"\n⏰ Resolves: {end_date}"
        f"\n🔗 [View on Polymarket]({url})"
    )


async def get_top_markets() -> list[tuple[dict, float]]:
    async with aiohttp.ClientSession() as session:
        markets = await fetch_markets(session)

    scored = [(m, score_market(m)) for m in markets]
    scored = [(m, s) for m, s in scored if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:TOP_N]


# ── Auth guard ───────────────────────────────────────────────────────────────

def restricted(func):
    """Only allow whitelisted users (if ALLOWED_USER_IDS is set)."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if ALLOWED_USER_IDS and uid not in ALLOWED_USER_IDS:
            await update.message.reply_text("⛔ Unauthorized.")
            log.warning("Blocked user %s", uid)
            return
        return await func(update, ctx)
    return wrapper


# ── Handlers ─────────────────────────────────────────────────────────────────

@restricted
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Polymarket Signal Bot*\n\n"
        "I scan Polymarket and surface high-probability trade setups.\n"
        "I do *not* trade for you — I just filter the noise.\n\n"
        "Commands:\n"
        "  /scan — Get top trade signals right now\n"
        "  /help — Show this message\n"
        "  /about — How scoring works"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@restricted
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


@restricted
async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📐 *Scoring Methodology*\n\n"
        "Each market is scored 0–1 across four factors:\n\n"
        "• *Price Edge (40%)* — Markets near 50¢ have the most uncertainty and opportunity. "
        "Near-certain markets (>85¢ or <15¢) are filtered out.\n\n"
        "• *Liquidity (30%)* — Higher liquidity = better fills and sharper prices.\n\n"
        "• *24h Volume (20%)* — Active markets reflect current information.\n\n"
        "• *Time to Resolution (10%)* — Markets resolving within 7 days get a boost; "
        "the catalyst is clear, reducing holding risk.\n\n"
        "Markets below $1,000 liquidity or $500 daily volume are excluded.\n\n"
        "_You always make the final call. The bot never touches your money._"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@restricted
async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Scanning Polymarket… please wait.")
    try:
        top = await get_top_markets()
        if not top:
            await msg.edit_text("😕 No qualifying markets found right now. Try again later.")
            return

        await msg.edit_text(
            f"✅ *Top {len(top)} Trade Signals*\n"
            f"_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
            "Sending each market below 👇",
            parse_mode="Markdown",
        )

        for rank, (market, score) in enumerate(top, start=1):
            text = format_market(market, rank, score)
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Open Market", url=f"https://polymarket.com/event/{market.get('slug','')}")],
            ])
            await update.message.reply_text(
                text,
                parse_mode="Markdown",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            await asyncio.sleep(0.3)   # Avoid Telegram rate limits

    except aiohttp.ClientError as e:
        log.error("Polymarket API error: %s", e)
        await msg.edit_text("❌ Failed to reach Polymarket API. Try again in a moment.")
    except Exception as e:
        log.exception("Unexpected error in /scan")
        await msg.edit_text(f"❌ Unexpected error: {e}")


# ── Scheduled auto-scan ──────────────────────────────────────────────────────

async def scheduled_scan(ctx: ContextTypes.DEFAULT_TYPE):
    """Push top signals every N hours to all allowed users."""
    if not ALLOWED_USER_IDS:
        return
    try:
        top = await get_top_markets()
        if not top:
            return

        header = (
            f"⏰ *Scheduled Signal Scan*\n"
            f"_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
            f"Top {len(top)} markets right now:"
        )

        for uid in ALLOWED_USER_IDS:
            await ctx.bot.send_message(uid, header, parse_mode="Markdown")
            for rank, (market, score) in enumerate(top, start=1):
                text = format_market(market, rank, score)
                await ctx.bot.send_message(
                    uid, text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
                await asyncio.sleep(0.3)
    except Exception:
        log.exception("scheduled_scan failed")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("scan",  cmd_scan))

    # Auto-scan every 6 hours (21600 seconds). Set first=60 so it runs 1 min after start.
    app.job_queue.run_repeating(scheduled_scan, interval=21600, first=60)

    log.info("Bot started. Listening for commands…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
