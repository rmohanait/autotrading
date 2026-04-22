"""
signal_engine.py — Ripster EMA Cloud signal calculator.

Pure calculation module — no API calls, no I/O.
Feed it price bars, get back BUY / SELL / HOLD signals.

Ripster's Rules:
  • ENTRY : price closes above EMA_FAST_LOWER (5) AND above EMA_BIAS_LOWER (34)
            on the bar AFTER it was below EMA_FAST_LOWER (crossover)
  • EXIT  : price closes below EMA_FAST_LOWER (5)
            on the bar AFTER it was above it (crossover down)
  • BIAS  : only take longs when price > EMA 34-50 cloud
            (no counter-trend trades)
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Deque
import config

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    """A single OHLCV price bar."""
    symbol: str
    timestamp: str
    open:  float
    high:  float
    low:   float
    close: float
    volume: float


@dataclass
class CloudState:
    """Snapshot of all EMA values and cloud positions for one bar."""
    ema5:  Optional[float] = None
    ema8:  Optional[float] = None
    ema9:  Optional[float] = None
    ema12: Optional[float] = None
    ema34: Optional[float] = None
    ema50: Optional[float] = None

    @property
    def fast_cloud_upper(self) -> Optional[float]:
        if self.ema5 and self.ema12:
            return max(self.ema5, self.ema12)
        return None

    @property
    def fast_cloud_lower(self) -> Optional[float]:
        if self.ema5 and self.ema12:
            return min(self.ema5, self.ema12)
        return None

    @property
    def bias_cloud_upper(self) -> Optional[float]:
        if self.ema34 and self.ema50:
            return max(self.ema34, self.ema50)
        return None

    @property
    def bias_cloud_lower(self) -> Optional[float]:
        if self.ema34 and self.ema50:
            return min(self.ema34, self.ema50)
        return None

    @property
    def fast_cloud_bullish(self) -> Optional[bool]:
        """True when EMA5 > EMA12 (fast trend is up)."""
        if self.ema5 and self.ema12:
            return self.ema5 > self.ema12
        return None

    @property
    def bias_bullish(self) -> Optional[bool]:
        """True when EMA34 > EMA50 (bias is bullish)."""
        if self.ema34 and self.ema50:
            return self.ema34 > self.ema50
        return None


@dataclass
class Signal:
    """A trading signal produced by the engine."""
    symbol: str
    action: str          # "BUY", "SELL", or "HOLD"
    price: float
    timestamp: str
    cloud: CloudState
    reason: str = ""
    confidence: float = 0.0   # 0.0–1.0, higher = stronger setup


class EMATracker:
    """
    Incremental EMA calculator.
    Maintains a rolling price buffer and updates EMAs bar-by-bar.
    """
    def __init__(self, period: int):
        self.period = period
        self.k = 2.0 / (period + 1)
        self.value: Optional[float] = None
        self._buffer: Deque[float] = deque(maxlen=period)
        self._initialized = False

    def update(self, price: float) -> Optional[float]:
        if not self._initialized:
            self._buffer.append(price)
            if len(self._buffer) == self.period:
                # Seed with simple moving average
                self.value = sum(self._buffer) / self.period
                self._initialized = True
        else:
            self.value = price * self.k + self.value * (1 - self.k)
        return self.value

    def seed(self, prices: list[float]) -> None:
        """Bulk-seed with historical prices (called on startup)."""
        for p in prices:
            self.update(p)

    @property
    def ready(self) -> bool:
        return self._initialized


class SignalEngine:
    """
    Per-symbol EMA Cloud signal engine.
    One instance per ticker being tracked.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bars_processed = 0

        # Six EMA trackers — Ripster's full cloud set
        self.ema5  = EMATracker(config.EMA_FAST_LOWER)
        self.ema8  = EMATracker(config.EMA_PULLBACK_LOWER)
        self.ema9  = EMATracker(config.EMA_PULLBACK_UPPER)
        self.ema12 = EMATracker(config.EMA_FAST_UPPER)
        self.ema34 = EMATracker(config.EMA_BIAS_LOWER)
        self.ema50 = EMATracker(config.EMA_BIAS_UPPER)

        # Previous bar state for crossover detection
        self._prev_close: Optional[float] = None
        self._prev_ema5:  Optional[float] = None
        self._prev_ema34: Optional[float] = None

        # Consecutive bars below EMA5 (used in "slow" exit mode)
        self._bars_below_ema5 = 0

        logger.info(f"[{symbol}] SignalEngine initialised")

    def seed(self, historical_closes: list[float]) -> None:
        """
        Seed EMAs with historical data before going live.
        Call this with the last 60+ closing prices on startup.
        """
        for ema in [self.ema5, self.ema8, self.ema9,
                    self.ema12, self.ema34, self.ema50]:
            ema.seed(historical_closes)
        if historical_closes:
            self._prev_close = historical_closes[-1]
            self._prev_ema5  = self.ema5.value
            self._prev_ema34 = self.ema34.value
        self.bars_processed = len(historical_closes)
        logger.info(f"[{self.symbol}] Seeded with {len(historical_closes)} historical bars")

    def update(self, bar: Bar) -> Signal:
        """
        Process one new bar. Returns a Signal (BUY / SELL / HOLD).
        """
        close = bar.close

        # Update all EMAs
        e5  = self.ema5.update(close)
        e8  = self.ema8.update(close)
        e9  = self.ema9.update(close)
        e12 = self.ema12.update(close)
        e34 = self.ema34.update(close)
        e50 = self.ema50.update(close)

        self.bars_processed += 1

        cloud = CloudState(
            ema5=e5, ema8=e8, ema9=e9,
            ema12=e12, ema34=e34, ema50=e50
        )

        # Need warmup before signals are reliable
        if self.bars_processed < config.EMA_WARMUP_BARS or not self.ema50.ready:
            self._update_prev(close, e5, e34)
            return Signal(self.symbol, "HOLD", close, bar.timestamp, cloud,
                          reason="warming up", confidence=0.0)

        signal = self._evaluate(close, cloud, bar.timestamp)
        self._update_prev(close, e5, e34)
        return signal

    def _evaluate(self, close: float, cloud: CloudState, ts: str) -> Signal:
        """Core Ripster signal logic."""
        prev_close = self._prev_close
        prev_ema5  = self._prev_ema5
        prev_ema34 = self._prev_ema34
        e5  = cloud.ema5
        e34 = cloud.ema34

        if any(v is None for v in [prev_close, prev_ema5, e5, e34]):
            return Signal(self.symbol, "HOLD", close, ts, cloud, reason="insufficient data")

        above_bias = close > e34
        was_below_fast = prev_close <= prev_ema5
        now_above_fast = close > e5

        # ── BUY signal ──────────────────────────────────────────────
        if was_below_fast and now_above_fast:
            if config.REQUIRE_ABOVE_BIAS_CLOUD and not above_bias:
                return Signal(self.symbol, "HOLD", close, ts, cloud,
                              reason="buy blocked — price below bias cloud (bearish)")

            confidence = self._buy_confidence(close, cloud)
            reason = (
                f"price crossed above EMA5 ({e5:.2f}) "
                f"while above bias cloud EMA34 ({e34:.2f})"
            )
            logger.info(f"[{self.symbol}] BUY signal @ ${close:.2f} | {reason}")
            return Signal(self.symbol, "BUY", close, ts, cloud,
                          reason=reason, confidence=confidence)

        # ── SELL / EXIT signal ────────────────────────────────────────
        was_above_fast = prev_close >= prev_ema5
        now_below_fast = close < e5

        if was_above_fast and now_below_fast:
            if config.EXIT_MODE == "slow":
                self._bars_below_ema5 += 1
                if self._bars_below_ema5 < 2:
                    return Signal(self.symbol, "HOLD", close, ts, cloud,
                                  reason=f"exit pending — 1 bar below EMA5 (need 2 for slow mode)")
            else:
                self._bars_below_ema5 = 0

            reason = f"price crossed below EMA5 ({e5:.2f}) — exit long"
            logger.info(f"[{self.symbol}] SELL signal @ ${close:.2f} | {reason}")
            return Signal(self.symbol, "SELL", close, ts, cloud,
                          reason=reason, confidence=0.85)
        else:
            self._bars_below_ema5 = 0

        return Signal(self.symbol, "HOLD", close, ts, cloud,
                      reason="no crossover detected")

    def _buy_confidence(self, close: float, cloud: CloudState) -> float:
        """
        Score the quality of a BUY signal (0.0–1.0).
        Higher score = cleaner Ripster setup.
        """
        score = 0.5  # Base

        # Bonus: fast cloud is bullish (EMA5 > EMA12)
        if cloud.fast_cloud_bullish:
            score += 0.15

        # Bonus: bias cloud is bullish (EMA34 > EMA50)
        if cloud.bias_bullish:
            score += 0.15

        # Bonus: price bounced off 8-9 pullback zone
        if cloud.ema8 and cloud.ema9:
            pullback_zone = min(cloud.ema8, cloud.ema9)
            if close > pullback_zone and abs(close - pullback_zone) / close < 0.003:
                score += 0.20  # Touched the 8-9 cloud — textbook Ripster pullback entry

        return min(score, 1.0)

    def _update_prev(self, close, e5, e34):
        self._prev_close = close
        self._prev_ema5  = e5
        self._prev_ema34 = e34

    @property
    def current_cloud(self) -> CloudState:
        return CloudState(
            ema5=self.ema5.value,  ema8=self.ema8.value,
            ema9=self.ema9.value,  ema12=self.ema12.value,
            ema34=self.ema34.value, ema50=self.ema50.value
        )

    @property
    def is_ready(self) -> bool:
        return self.bars_processed >= config.EMA_WARMUP_BARS and self.ema50.ready


class MultiSymbolEngine:
    """
    Manages one SignalEngine per symbol in the watchlist.
    Single point of entry for the main trading loop.
    """

    def __init__(self):
        self._engines: dict[str, SignalEngine] = {}

    def get_engine(self, symbol: str) -> SignalEngine:
        if symbol not in self._engines:
            self._engines[symbol] = SignalEngine(symbol)
        return self._engines[symbol]

    def seed(self, symbol: str, historical_closes: list[float]) -> None:
        self.get_engine(symbol).seed(historical_closes)

    def update(self, bar: Bar) -> Signal:
        return self.get_engine(bar.symbol).update(bar)

    def is_ready(self, symbol: str) -> bool:
        return self.get_engine(symbol).is_ready

    @property
    def symbols(self) -> list[str]:
        return list(self._engines.keys())
