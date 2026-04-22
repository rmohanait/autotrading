"""
main.py — Ripster Trader — Main async orchestrator.

What this does every market day:
  1. At startup: fetch Ripster's latest picks (TradingView or Twitter)
  2. Seed EMAs with historical bar data for each ticker
  3. Subscribe to live 5-min bar stream via Alpaca WebSocket
  4. On each bar: calculate EMA clouds → check for signal
  5. On BUY signal: check news → check circuit breaker → SMS alert → place order
  6. On SELL signal: exit position → log trade
  7. At market close: close all positions → send daily summary email

Run:
    python main.py

Stop:
    Ctrl+C  (triggers graceful shutdown — closes positions, sends summary)
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
import pytz

import config
from data_feed import DataFeed
from signal_engine import MultiSymbolEngine, Bar
from order_manager import OrderManager
from news_filter import NewsFilter
from notifier import Notifier
from discovery import StockDiscovery
from logger import TradeLogger

# ── Logging setup ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

ET = pytz.timezone("America/New_York")


def is_market_open() -> bool:
    """Check if the US stock market is currently open (simplified check)."""
    now = datetime.now(ET)
    # Skip weekends
    if now.weekday() >= 5:
        return False
    # Market hours with configured buffer
    open_mins  = config.MARKET_OPEN_HOUR  * 60 + config.MARKET_OPEN_MIN  + config.SKIP_OPEN_MINUTES
    close_mins = config.MARKET_CLOSE_HOUR * 60 + config.MARKET_CLOSE_MIN
    now_mins   = now.hour * 60 + now.minute
    return open_mins <= now_mins <= close_mins


def is_near_close() -> bool:
    """True when we're within 5 minutes of the configured market close."""
    now      = datetime.now(ET)
    now_mins = now.hour * 60 + now.minute
    close_mins = config.MARKET_CLOSE_HOUR * 60 + config.MARKET_CLOSE_MIN
    return abs(now_mins - close_mins) <= 5


class RipsterTrader:
    """
    Main trading system. Ties all modules together.
    """

    def __init__(self):
        logger.info("=" * 60)
        logger.info("  RIPSTER TRADER  |  " +
                    ("PAPER" if config.IS_PAPER_TRADING else "⚠️  LIVE") + " MODE")
        logger.info("=" * 60)

        self.feed          = DataFeed()
        self.engine        = MultiSymbolEngine()
        self.orders        = OrderManager()
        self.news          = NewsFilter()
        self.notifier      = Notifier()
        self.discovery     = StockDiscovery()
        self.db            = TradeLogger()
        self._watchlist:   list[str] = []
        self._running      = True
        self._shutdown_evt = asyncio.Event()

    # ── Startup ──────────────────────────────────────────────────────

    async def startup(self):
        """Fetch watchlist, seed EMAs, and log startup event."""
        logger.info("Starting up…")

        # 1. Discover tickers
        self._watchlist = self.discovery.get_ripster_picks()
        logger.info(f"Watchlist: {self._watchlist}")

        # 2. Fetch historical bars and seed each symbol's EMA engine
        for symbol in self._watchlist:
            closes = self.feed.get_historical_closes(symbol, bars=config.EMA_WARMUP_BARS + 10)
            if closes:
                self.engine.seed(symbol, closes)
                logger.info(f"  [{symbol}] EMA seeded with {len(closes)} bars ✓")
            else:
                logger.warning(f"  [{symbol}] Could not seed EMAs — signals may be delayed")

        # 3. Log startup
        mode = "PAPER" if config.IS_PAPER_TRADING else "LIVE"
        self.db.log_event("STARTUP", f"{mode} | watchlist={self._watchlist}")
        self.notifier.send_info(
            f"[{mode}] Ripster Trader started\n"
            f"Watching: {', '.join(self._watchlist)}\n"
            f"Portfolio: ${self.orders.portfolio_value:,.2f}"
        )

        logger.info(f"System ready. Market open: {is_market_open()}")
        logger.info(f"Portfolio: ${self.orders.portfolio_value:,.2f}")
        logger.info("-" * 60)

    # ── Core bar handler ─────────────────────────────────────────────

    async def on_bar(self, bar: Bar):
        """Called by DataFeed for every new completed bar."""

        # Skip if market shouldn't be open
        if not is_market_open():
            return

        # End-of-day close
        if is_near_close():
            await self._end_of_day()
            return

        # Process through signal engine
        sig = self.engine.update(bar)

        logger.debug(
            f"[{bar.symbol}] {bar.timestamp} close=${bar.close:.2f} "
            f"→ {sig.action} (conf={sig.confidence:.2f}) | {sig.reason}"
        )

        # ── BUY signal ────────────────────────────────────────────
        if sig.action == "BUY" and not self.orders.has_position(bar.symbol):
            await self._handle_buy_signal(sig, bar)

        # ── SELL signal ───────────────────────────────────────────
        elif sig.action == "SELL" and self.orders.has_position(bar.symbol):
            await self._handle_sell_signal(sig, bar)

    async def _handle_buy_signal(self, sig, bar):
        symbol = bar.symbol
        logger.info(f"[{symbol}] ▲ BUY signal @ ${sig.price:.2f} | conf={sig.confidence:.2f}")

        # Check circuit breaker
        allowed, cb_reason = self.orders.circuit_breaker.can_trade()
        if not allowed:
            logger.warning(f"[{symbol}] BUY blocked by circuit breaker: {cb_reason}")
            self.db.log_signal(symbol, "BUY", sig.price, sig.timestamp,
                               sig.reason, sig.confidence, False, cb_reason)
            return

        # Check news sentiment
        news_safe, news_reason = self.news.is_safe_to_trade(symbol)
        if not news_safe:
            logger.warning(f"[{symbol}] BUY blocked by news filter: {news_reason}")
            self.db.log_signal(symbol, "BUY", sig.price, sig.timestamp,
                               sig.reason, sig.confidence, False, news_reason)
            self.notifier.send_info(f"[{symbol}] Trade blocked by news: {news_reason}")
            return

        # Log signal (before confirmation)
        self.db.log_signal(symbol, "BUY", sig.price, sig.timestamp,
                           sig.reason, sig.confidence, True, "")

        # SMS confirmation with cancel window
        proceed = await self.notifier.alert_with_cancel_window(
            symbol, "BUY", sig.price,
            reason=sig.reason, confidence=sig.confidence
        )
        if not proceed:
            logger.info(f"[{symbol}] BUY cancelled by user via SMS STOP")
            self.db.log_event("USER_CANCEL", f"BUY {symbol} @ ${sig.price:.2f}")
            return

        # Place order
        result = self.orders.buy(symbol, sig.price)
        if result.success:
            self.db.log_entry(
                symbol=symbol, qty=result.qty, price=result.price,
                order_id=result.order_id, signal_reason=sig.reason,
                cloud_ema5=sig.cloud.ema5, cloud_ema34=sig.cloud.ema34,
                news_safe=news_safe, news_reason=news_reason,
                confidence=sig.confidence,
            )
            logger.info(f"[{symbol}] ✅ BUY placed: {result.qty} shares @ ${result.price:.2f}")
        else:
            logger.error(f"[{symbol}] BUY order failed: {result.reason}")
            self.notifier.send_info(f"[{symbol}] BUY order failed: {result.reason}")

    async def _handle_sell_signal(self, sig, bar):
        symbol = bar.symbol
        logger.info(f"[{symbol}] ▼ SELL signal @ ${sig.price:.2f}")

        self.db.log_signal(symbol, "SELL", sig.price, sig.timestamp,
                           sig.reason, sig.confidence, True, "")

        result = self.orders.sell(symbol, sig.price)
        if result.success:
            self.db.log_exit(symbol, sig.price, result.order_id, sig.reason)
            pos_entry = self.orders.circuit_breaker  # For P&L reporting

            # Notify
            self.notifier.send_info(
                f"[{'PAPER' if config.IS_PAPER_TRADING else 'LIVE'}] "
                f"SOLD {symbol} @ ${sig.price:.2f}"
            )
            logger.info(f"[{symbol}] ✅ SELL executed @ ${sig.price:.2f}")
        else:
            logger.error(f"[{symbol}] SELL order failed: {result.reason}")

    # ── End of day ───────────────────────────────────────────────────

    async def _end_of_day(self):
        if not hasattr(self, "_eod_done"):
            self._eod_done = False
        if self._eod_done:
            return

        logger.info("═" * 60)
        logger.info("  END OF DAY — closing all positions")
        logger.info("═" * 60)
        self._eod_done = True

        # Close all open positions
        results = self.orders.close_all_positions()
        for r in results:
            if r.success:
                self.db.log_exit(r.symbol, r.price, r.order_id, "EOD close")

        # Send daily summary
        today_trades = self.db.get_today_trades()
        headlines = {}
        for sym in self._watchlist:
            headlines[sym] = self.news.get_headlines(sym)

        self.notifier.send_daily_summary(
            trades=today_trades,
            portfolio_value=self.orders.portfolio_value,
            daily_pnl_pct=self.orders.circuit_breaker.daily_pnl_pct,
            watchlist=self._watchlist,
            headlines=headlines,
        )

        summary = self.orders.status_summary()
        logger.info(f"EOD Summary: {summary}")
        self.db.log_event("EOD", summary)

    # ── Periodic tasks ────────────────────────────────────────────────

    async def _periodic_discovery(self):
        """Refresh the watchlist every DISCOVERY_INTERVAL_MINUTES minutes."""
        interval = config.DISCOVERY_INTERVAL_MINUTES * 60
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                break
            logger.info("Refreshing watchlist from Ripster's channels…")
            new_list = self.discovery.get_ripster_picks()
            added = [s for s in new_list if s not in self._watchlist]
            if added:
                logger.info(f"New tickers added to watchlist: {added}")
                self._watchlist = new_list
                # Seed EMAs for new tickers
                for sym in added:
                    closes = self.feed.get_historical_closes(sym, bars=config.EMA_WARMUP_BARS + 10)
                    if closes:
                        self.engine.seed(sym, closes)

    async def _heartbeat(self):
        """Log a heartbeat every 30 minutes so you know the system is alive."""
        while self._running:
            await asyncio.sleep(1800)
            if not self._running:
                break
            logger.info(f"♥ HEARTBEAT | {self.orders.status_summary()}")

    # ── Run ──────────────────────────────────────────────────────────

    async def run(self):
        await self.startup()

        # Start background tasks
        discovery_task = asyncio.create_task(self._periodic_discovery())
        heartbeat_task  = asyncio.create_task(self._heartbeat())

        # Start live data feed (runs in executor since it's blocking)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.feed.start(self._watchlist, self.on_bar)
            )
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            discovery_task.cancel()
            heartbeat_task.cancel()
            await self._shutdown()

    async def _shutdown(self):
        logger.info("Shutting down…")
        if not getattr(self, "_eod_done", False):
            await self._end_of_day()
        self.feed.stop()
        self.db.log_event("SHUTDOWN", "Clean shutdown")
        logger.info("Goodbye.")


# ── Entry point ──────────────────────────────────────────────────

def main():
    if not config.ALPACA_API_KEY or config.ALPACA_API_KEY.startswith("PK"):
        if config.ALPACA_API_KEY == "PKXXXXXXXXXXXXXXXXXXXXXXXX":
            print("\n❌  ERROR: Please fill in your .env file before running.")
            print("   Copy .env.example → .env and add your API keys.\n")
            sys.exit(1)

    trader = RipsterTrader()

    def _handle_signal(signum, frame):
        logger.info("Interrupt received — shutting down gracefully…")
        trader._running = False
        trader.feed.stop()

    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
