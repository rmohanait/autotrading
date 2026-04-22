"""
data_feed.py — Alpaca real-time data feed + historical bar seeding.

On startup:
  1. Fetches the last 100 bars of historical data for each symbol (seeds the EMAs).
  2. Opens a WebSocket and streams live bars going forward.
  3. Calls an on_bar(Bar) callback for every new bar received.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Awaitable

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.live import StockDataStream
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from signal_engine import Bar
import config

logger = logging.getLogger(__name__)

# Map config string to Alpaca TimeFrame
TIMEFRAME_MAP = {
    "1Min":  TimeFrame(1,  TimeFrameUnit.Minute),
    "5Min":  TimeFrame(5,  TimeFrameUnit.Minute),
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
}


class DataFeed:
    """
    Wraps Alpaca's historical + live bar APIs.
    Usage:
        feed = DataFeed()
        historical = feed.get_historical_closes("TSLA", bars=100)
        feed.start(symbols=["TSLA"], on_bar=my_callback)
    """

    def __init__(self):
        self._hist_client = StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
        )
        self._stream = StockDataStream(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
        )
        self._timeframe = TIMEFRAME_MAP.get(config.BAR_TIMEFRAME, TimeFrame(5, TimeFrameUnit.Minute))
        self._on_bar_callback: Callable[[Bar], Awaitable[None]] | None = None

    # ── Historical Data ──────────────────────────────────────────────

    def get_historical_closes(self, symbol: str, bars: int = 100) -> list[float]:
        """
        Fetch the last `bars` closing prices for `symbol`.
        Used to seed the EMA calculations before going live.
        """
        try:
            end   = datetime.now(timezone.utc)
            # Request extra bars to account for weekends/holidays
            start = end - timedelta(days=max(bars // 6, 10))

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=self._timeframe,
                start=start,
                end=end,
                limit=bars,
            )
            bars_df = self._hist_client.get_stock_bars(request).df

            if bars_df.empty:
                logger.warning(f"[{symbol}] No historical data returned")
                return []

            # Flatten multi-index if needed
            if hasattr(bars_df.index, 'levels'):
                closes = bars_df.xs(symbol, level='symbol')['close'].tolist()
            else:
                closes = bars_df['close'].tolist()

            logger.info(f"[{symbol}] Fetched {len(closes)} historical bars for EMA seeding")
            return closes

        except Exception as e:
            logger.error(f"[{symbol}] Failed to fetch historical data: {e}")
            return []

    def get_historical_bars(self, symbol: str, bars: int = 100) -> list[Bar]:
        """
        Returns full Bar objects (used for backtesting / replay).
        """
        try:
            end   = datetime.now(timezone.utc)
            start = end - timedelta(days=max(bars // 6, 10))

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=self._timeframe,
                start=start,
                end=end,
                limit=bars,
            )
            bars_df = self._hist_client.get_stock_bars(request).df

            if bars_df.empty:
                return []

            if hasattr(bars_df.index, 'levels'):
                sym_df = bars_df.xs(symbol, level='symbol').reset_index()
            else:
                sym_df = bars_df.reset_index()

            result = []
            for _, row in sym_df.iterrows():
                result.append(Bar(
                    symbol=symbol,
                    timestamp=str(row.get('timestamp', row.get('index', ''))),
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=float(row.get('volume', 0)),
                ))
            return result

        except Exception as e:
            logger.error(f"[{symbol}] Failed to fetch bar objects: {e}")
            return []

    # ── Live Streaming ───────────────────────────────────────────────

    def start(
        self,
        symbols: list[str],
        on_bar: Callable[[Bar], Awaitable[None]],
    ) -> None:
        """
        Start streaming live bars. Blocks until stopped.
        `on_bar` is called with each new completed bar.
        """
        self._on_bar_callback = on_bar

        async def _handle_bar(raw_bar):
            """Convert Alpaca bar to our Bar dataclass and call the callback."""
            try:
                bar = Bar(
                    symbol=raw_bar.symbol,
                    timestamp=str(raw_bar.timestamp),
                    open=float(raw_bar.open),
                    high=float(raw_bar.high),
                    low=float(raw_bar.low),
                    close=float(raw_bar.close),
                    volume=float(raw_bar.volume),
                )
                if self._on_bar_callback:
                    await self._on_bar_callback(bar)
            except Exception as e:
                logger.error(f"Error processing bar for {raw_bar.symbol}: {e}")

        self._stream.subscribe_bars(_handle_bar, *symbols)
        logger.info(f"Live data stream started for: {', '.join(symbols)}")

        try:
            self._stream.run()
        except KeyboardInterrupt:
            logger.info("Data feed stopped by user")
        except Exception as e:
            logger.error(f"Data feed error: {e}")
            raise

    def stop(self) -> None:
        """Gracefully stop the WebSocket stream."""
        try:
            self._stream.stop()
            logger.info("Data feed stopped")
        except Exception as e:
            logger.warning(f"Error stopping data feed: {e}")
