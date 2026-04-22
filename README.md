# Ripster Trader 🤖📈

Fully automated day trading system based on **Ripster47's EMA Cloud** methodology.

**What it does:**
- Monitors Ripster's TradingView/Twitter for new stock picks
- Calculates 5-12, 8-9, and 34-50 EMA clouds in real time (Alpaca WebSocket)
- Fires BUY/SELL signals using Ripster's exact crossover rules
- Checks live news sentiment before every trade (blocks on negative news)
- SMS alert with 60-second cancel window before each order fires
- Places orders on your **Alpaca paper account** (switch to live when ready)
- Logs every trade to SQLite + generates a daily HTML performance dashboard

---

## 🚀 Quick Setup (< 30 minutes)

### Step 1 — Python environment

```bash
cd ripster_trader
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Step 2 — API keys (free)

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Key | Where to get it | Cost |
|-----|----------------|------|
| `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` | [alpaca.markets](https://alpaca.markets) → Paper Trading | **Free** |
| `NEWS_API_KEY` | [marketaux.com](https://marketaux.com) → Free tier | **Free** (100 req/day) |
| `TWILIO_*` keys | [twilio.com](https://twilio.com) → trial account | **~$1/mo** |
| `TWITTER_BEARER_TOKEN` | [developer.x.com](https://developer.x.com) (optional) | ~$15–25/mo |

> **Tip:** Leave `TWITTER_BEARER_TOKEN` blank — the system will scrape Ripster's
> TradingView ideas page for free instead.

### Step 3 — Configure trading parameters

Open `config.py` and review:

```python
DEFAULT_WATCHLIST    = ["TSLA"]        # Tickers to monitor
POSITION_SIZE_PCT    = 0.05            # 5% of portfolio per trade
MAX_TRADES_PER_DAY   = 3              # Safety limit
MAX_DAILY_LOSS_PCT   = 0.02           # Kill switch at -2% daily
REQUIRE_SMS_CONFIRMATION = True        # SMS cancel window (recommended: True)
```

### Step 4 — Run it

```bash
python main.py
```

You'll see live output like:
```
09:45:00 [INFO] main: Watchlist: ['TSLA']
09:45:01 [INFO] main: [TSLA] EMA seeded with 70 historical bars ✓
09:45:02 [INFO] main: System ready. Market open: True
09:50:00 [INFO] signal_engine: [TSLA] BUY signal @ $401.20 | price crossed above EMA5...
09:50:00 [INFO] main: SMS alert sent. Waiting 60s for STOP reply...
09:51:00 [INFO] main: [TSLA] ✅ BUY placed: 12 shares @ $401.20
```

### Step 5 — View your dashboard

```bash
python report.py
```

Opens a performance dashboard in your browser.

---

## 📱 SMS Cancel Window

When a trade signal fires, you'll receive a text like:

> `[PAPER] BUY TSLA @ $401.20 | Conf: 85%`
> `price crossed above EMA5 (399.80) while above bias cloud`
> `Reply STOP within 60s to cancel.`

**Reply STOP** to your Twilio number within 60 seconds to cancel the trade.
If you don't reply, the order is placed automatically.

---

## 📸 Using Ripster Screenshots

When you see an interesting post on Ripster's Twitter/X:

1. Take a screenshot
2. Paste it into your Claude chat
3. Ask: *"Extract the tickers from this Ripster post"*
4. Claude returns the list of tickers
5. Add them to `DEFAULT_WATCHLIST` in `config.py` (or restart main.py)

---

## 🧪 Paper Trading Phase (Recommended: 4–8 weeks)

The system defaults to Alpaca **paper trading** — real market data, fake money.

**What to measure during paper trading:**
- Win rate (aim for >50%)
- Profit factor (aim for >1.5)
- Average win vs average loss (win should be ≥ 1.5× loss)
- Which times of day produce best signals
- Which news patterns correlate with bad trades

**After 4–6 weeks**, review your `report.html` dashboard. If the numbers look good,
switch to live:

1. Sign up for a live Alpaca account (or set up TradersPost → E*TRADE)
2. In `.env`, change `ALPACA_BASE_URL` to `https://api.alpaca.markets`
3. Replace the paper API keys with your live API keys
4. Start with `POSITION_SIZE_PCT = 0.02` (2%) — very small until proven

---

## 🏗️ Architecture

```
main.py
  ├── discovery.py      ← Finds tickers (TradingView / Twitter / Screenshot)
  ├── data_feed.py      ← Alpaca WebSocket (live 5-min bars)
  ├── signal_engine.py  ← EMA cloud calculator (Ripster's rules)
  ├── news_filter.py    ← Marketaux news sentiment check
  ├── order_manager.py  ← Alpaca order placement + circuit breaker
  ├── notifier.py       ← Twilio SMS + email alerts
  ├── logger.py         ← SQLite trade database
  └── report.py         ← HTML performance dashboard
```

---

## 🔧 Tuning Parameters

After paper trading, you can tune these in `config.py`:

| Parameter | Default | What it controls |
|-----------|---------|-----------------|
| `POSITION_SIZE_PCT` | 5% | How much of portfolio per trade |
| `MAX_TRADES_PER_DAY` | 3 | Daily trade limit |
| `MAX_DAILY_LOSS_PCT` | 2% | Kill switch threshold |
| `CANCEL_WINDOW_SECONDS` | 60 | Seconds before auto-execute |
| `EXIT_MODE` | "fast" | "fast" or "slow" exit on EMA5 break |
| `SKIP_OPEN_MINUTES` | 15 | Skip first N minutes after open |
| `NEWS_LOOKBACK_HOURS` | 2 | How far back to check news |

---

## 🔄 Going Live with Power E*TRADE

When you're ready to move from Alpaca to Power E*TRADE:

1. Sign up at [TradersPost.io](https://traderspost.io) (~$49/mo)
2. Connect your E*TRADE account to TradersPost
3. Set up a TradersPost webhook URL
4. In `notifier.py`, add a webhook call to TradersPost alongside the SMS alert
5. TradersPost receives the signal and routes the order to E*TRADE

The signal engine and all logic stays the same — only the execution endpoint changes.

---

## ⚠️ Disclaimer

This software is for educational and research purposes.
Automated trading carries significant financial risk.
Past paper trading performance does not guarantee future live results.
Always supervise automated systems. This is not financial advice.
