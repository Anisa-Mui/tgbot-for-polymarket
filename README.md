# 📊 Polymarket Signal Bot — Setup Guide

> "Let it filter high-probability trades for you, then YOU decide which ones to take."

This bot scans Polymarket in real time, scores every active market for trade opportunity,
and sends you the top picks on Telegram. It **never touches your money**.

---

## How It Works

1. Calls Polymarket's public API (`gamma-api.polymarket.com`)
2. Scores each market on 4 factors (see `/about` in the bot)
3. Filters out near-certain markets, low-liquidity, and low-volume markets
4. Sends you the **top 10 signals** with price bars, liquidity, volume, and a link
5. Repeats automatically every 6 hours (configurable)

---

## Prerequisites

| Requirement | Where to get it |
|---|---|
| Python 3.11+ | https://python.org |
| A Telegram account | https://telegram.org |
| A Telegram Bot token | Message **@BotFather** → `/newbot` |
| Your Telegram user ID | Message **@userinfobot** |

---

## Step-by-Step Setup

### Step 1 — Create your Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Give it a name (e.g., "My Polymarket Bot") and a username (e.g., `mypolybot`)
4. BotFather will give you a token like: `7123456789:AAFxxxxxxxxxxxxxxxxxxxxx`
5. **Copy this token** — you'll need it next

### Step 2 — Get your Telegram user ID

1. Message **@userinfobot** on Telegram
2. It will reply with your numeric user ID (e.g., `123456789`)
3. Copy it

### Step 3 — Set up the project

```bash
# Create a project folder
mkdir polymarket-bot && cd polymarket-bot

# Copy bot.py and requirements.txt here
# (the files you downloaded)

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Step 4 — Configure environment variables

```bash
# Copy the example env file
cp .env.example .env

# Edit .env with your values:
TELEGRAM_BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxxx
ALLOWED_USER_IDS=123456789

# Load the variables
export $(cat .env | xargs)
```

### Step 5 — Run the bot

```bash
python bot.py
```

You should see:
```
2024-xx-xx | INFO     | Bot started. Listening for commands…
```

### Step 6 — Test it

Open Telegram, find your bot by username, and send:
- `/start` — welcome message
- `/scan` — immediate signal scan
- `/about` — scoring explanation

---

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and command list |
| `/scan` | Fetch top trade signals right now |
| `/about` | Explains the scoring algorithm |
| `/help` | Same as `/start` |

---

## Auto-Scan Schedule

The bot automatically pushes signals every **6 hours**.
To change this, edit the `interval=` value in `bot.py`:

```python
# Line near the bottom of main()
app.job_queue.run_repeating(scheduled_scan, interval=21600, first=60)
#                                                    ^ seconds (21600 = 6h)
```

---

## Deploying 24/7 (so it runs when your computer is off)

### Option A — Railway (easiest, free tier available)
1. Go to https://railway.app
2. Create a new project → "Deploy from GitHub"
3. Push your files to a GitHub repo
4. Add environment variables in Railway dashboard:
   - `TELEGRAM_BOT_TOKEN`
   - `ALLOWED_USER_IDS`
5. Railway will auto-detect Python and run `python bot.py`

### Option B — VPS (DigitalOcean, Hetzner, etc.)
```bash
# On your server:
git clone your-repo
cd polymarket-bot
pip install -r requirements.txt

# Run with screen so it survives disconnects
screen -S polybot
export TELEGRAM_BOT_TOKEN=xxx
export ALLOWED_USER_IDS=xxx
python bot.py
# Ctrl+A then D to detach
```

### Option C — Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt bot.py ./
RUN pip install -r requirements.txt
CMD ["python", "bot.py"]
```
```bash
docker build -t polybot .
docker run -e TELEGRAM_BOT_TOKEN=xxx -e ALLOWED_USER_IDS=xxx polybot
```

---

## Scoring Algorithm

Each market scored 0–1 from four factors:

| Factor | Weight | Logic |
|---|---|---|
| Price Edge | 40% | Markets near 50¢ have maximum uncertainty — most tradeable |
| Liquidity | 30% | Higher = better fills, sharper prices |
| 24h Volume | 20% | Active markets reflect current information |
| Time to Close | 10% | Resolving within 7 days = clear catalyst |

**Filters applied:**
- Markets >85¢ or <15¢ probability excluded (near-certain = no edge)
- Minimum $1,000 liquidity
- Minimum $500 daily volume

---

## Customization

| What to change | Where in `bot.py` |
|---|---|
| Number of markets shown | `TOP_N = 10` |
| Minimum liquidity | `MIN_LIQUIDITY = 1_000` |
| Minimum daily volume | `MIN_VOLUME_24H = 500` |
| Near-certain filter | `MAX_PRICE_EDGE = 0.15` |
| Auto-scan frequency | `interval=21600` in `main()` |

---

## Troubleshooting

**Bot doesn't respond:**
- Check your `TELEGRAM_BOT_TOKEN` is correct
- Make sure you messaged *your* bot, not BotFather
- Confirm your user ID is in `ALLOWED_USER_IDS`

**"No qualifying markets found":**
- Try lowering `MIN_LIQUIDITY` or `MIN_VOLUME_24H`
- Polymarket API may be temporarily slow — try again

**API errors:**
- Polymarket's free API has rate limits; the bot is well within them
- Check your internet connection

---

*This bot is for informational purposes only. Not financial advice.*
*Always do your own research before making any trade.*
