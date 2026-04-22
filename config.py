"""
config.py — All trading parameters in one place.
Edit this file to tune the system. No other files need changing for basic customization.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────
#  API CREDENTIALS  (loaded from .env — don't hardcode here)
# ─────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
IS_PAPER_TRADING  = "paper" in ALPACA_BASE_URL

TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER  = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_TO_NUMBER    = os.getenv("TWILIO_TO_NUMBER", "")

NEWS_API_KEY          = os.getenv("NEWS_API_KEY", "")
TWITTER_BEARER_TOKEN  = os.getenv("TWITTER_BEARER_TOKEN", "")

EMAIL_FROM         = os.getenv("EMAIL_FROM", "")
EMAIL_TO           = os.getenv("EMAIL_TO", "")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")

# ─────────────────────────────────────────────────────────────
#  WATCHLIST — stocks the system monitors
# ─────────────────────────────────────────────────────────────
# Core watchlist of liquid, high-volume stocks that work well
# with Ripster's EMA Cloud system. Add/remove as needed.
# The discovery module will append tickers from Ripster's posts
# but always starts from this base list.
DEFAULT_WATCHLIST = [
    "TSLA",   # Tesla — Ripster's most-discussed stock
    "NVDA",   # Nvidia — high momentum, EMA cloud works well
    "AAPL",   # Apple — liquid, reliable signals
    "AMD",    # AMD — volatile, good for EMA entries
    "SPY",    # S&P 500 ETF — market direction reference
    "QQQ",    # Nasdaq ETF — tech trend filter
    "MSFT",   # Microsoft — steady trends
    "META",   # Meta — strong momentum stock
]

# ─────────────────────────────────────────────────────────────
#  SIGNAL ENGINE — Ripster EMA Cloud parameters
# ─────────────────────────────────────────────────────────────
# Fast trend cloud (primary entry/exit trigger)
EMA_FAST_LOWER = 5
EMA_FAST_UPPER = 12

# Pullback zone cloud (secondary entry on dips)
EMA_PULLBACK_LOWER = 8
EMA_PULLBACK_UPPER = 9

# Bias cloud (determines bull vs bear direction — no counter-trend trades)
EMA_BIAS_LOWER = 34
EMA_BIAS_UPPER = 50

# Minimum bars needed before signals are valid (seeds the EMA calculations)
EMA_WARMUP_BARS = 60

# Bar timeframe for signal calculation
BAR_TIMEFRAME = "5Min"   # Options: "1Min", "5Min", "15Min"

# ─────────────────────────────────────────────────────────────
#  ENTRY / EXIT RULES
# ─────────────────────────────────────────────────────────────
# Entry: price must close above BOTH EMA_FAST_LOWER AND EMA_BIAS_LOWER
# Exit:  price must close below EMA_FAST_LOWER

# Additional entry filters:
REQUIRE_ABOVE_BIAS_CLOUD = True   # Only long when price > EMA 34 (bullish bias)
REQUIRE_VOLUME_CONFIRMATION = False  # Set True to require above-avg volume (advanced)

# Exit aggression: "fast" exits on fast cloud break, "slow" waits for 2 consecutive bars
EXIT_MODE = "fast"

# ─────────────────────────────────────────────────────────────
#  POSITION SIZING
# ─────────────────────────────────────────────────────────────
# Percentage of total portfolio value to use per trade
POSITION_SIZE_PCT = 0.05        # 5% of portfolio per trade (conservative start)
MAX_POSITION_SIZE_PCT = 0.10    # Never exceed 10% in a single position

# Use limit orders (safer) vs market orders (faster fill)
USE_LIMIT_ORDERS = True
# How many cents above ask to place limit buy (to ensure fill)
LIMIT_ORDER_OFFSET_CENTS = 0.05

# ─────────────────────────────────────────────────────────────
#  CIRCUIT BREAKER — hard safety limits
# ─────────────────────────────────────────────────────────────
MAX_TRADES_PER_DAY   = 3        # Stop trading after this many trades in one day
MAX_DAILY_LOSS_PCT   = 0.02     # Kill switch: halt if portfolio drops 2% in one day
MAX_OPEN_POSITIONS   = 2        # Never hold more than this many positions at once

# ─────────────────────────────────────────────────────────────
#  ALERTS & CONFIRMATION
# ─────────────────────────────────────────────────────────────
# If True, sends SMS before each trade and waits CANCEL_WINDOW_SECONDS.
# Reply STOP to the Twilio number within the window to cancel the trade.
REQUIRE_SMS_CONFIRMATION = True
CANCEL_WINDOW_SECONDS    = 60

# Send daily summary email at market close
SEND_DAILY_EMAIL_SUMMARY = True

# ─────────────────────────────────────────────────────────────
#  NEWS FILTER
# ─────────────────────────────────────────────────────────────
ENABLE_NEWS_FILTER     = True
# Block trade if negative news found in the last N hours
NEWS_LOOKBACK_HOURS    = 2
# Keywords that trigger a news block (case-insensitive)
NEGATIVE_KEYWORDS = [
    "recall", "lawsuit", "investigation", "fraud", "bankruptcy",
    "crash", "scandal", "sec", "subpoena", "downgrade", "miss",
    "layoff", "loss", "decline", "warning", "halt"
]

# ─────────────────────────────────────────────────────────────
#  STOCK DISCOVERY
# ─────────────────────────────────────────────────────────────
# How often to check for new Ripster picks (minutes)
DISCOVERY_INTERVAL_MINUTES = 30

# If Twitter bearer token is set, use X API. Otherwise use TradingView scraper.
USE_TWITTER_FOR_DISCOVERY = bool(TWITTER_BEARER_TOKEN)
RIPSTER_TWITTER_USERNAME  = "ripster47"
RIPSTER_TRADINGVIEW_URL   = "https://www.tradingview.com/u/ripster47/#ideas"

# Set to False to disable auto-scraping and use only DEFAULT_WATCHLIST.
# The TradingView scraper can pick up nav/menu words — disable until improved.
USE_SCRAPER_DISCOVERY = False

# ─────────────────────────────────────────────────────────────
#  MARKET HOURS (Eastern Time)
# ─────────────────────────────────────────────────────────────
MARKET_OPEN_HOUR   = 9
MARKET_OPEN_MIN    = 30
MARKET_CLOSE_HOUR  = 15
MARKET_CLOSE_MIN   = 45   # Stop 15 min before close to avoid end-of-day traps

# Avoid first N minutes of market open (high volatility, unreliable signals)
SKIP_OPEN_MINUTES = 15

# ─────────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────────
DB_PATH = "trades.db"
LOG_PATH = "ripster_trader.log"
