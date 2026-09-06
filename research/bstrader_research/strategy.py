"""
strategy.py — position-generating strategies for the backtester.

A Strategy maps a stream of bars to a target position each bar:
    +1 = long, 0 = flat, -1 = short.

The backtester (backtest.py) turns changes in target position into trades and
applies costs and session rules. Strategies here are pure decision logic.

RipsterBaseline reuses the LIVE signal engine (`signal_engine.SignalEngine`) so
the backtest measures the actual deployed logic, not a re-implementation. This is
the whole point of Phase 2: an honest baseline of what already runs.

SignalOverlay wraps any base strategy and applies a BigShort-derived filter or
tilt on top of it, so Phases 3-8 can be expressed as "baseline + one variable"
without duplicating the baseline. Overlays receive the aligned feature row for
the current bar (or None if no captured data), and can only *gate* or *flip*
positions — they cannot see the future.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Optional, Protocol

import pandas as pd

# Make the repo root importable so we can reuse the live engine unchanged.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


class Strategy(Protocol):
    name: str

    def target_position(self, i: int, bars: pd.DataFrame,
                        features: Optional[pd.Series]) -> int:
        """Return desired position for bar i: +1 long, 0 flat, -1 short."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
#  Baseline: reuse the live Ripster EMA-cloud engine
# ─────────────────────────────────────────────────────────────────────────────
class RipsterBaseline:
    """
    Wraps the live `signal_engine.SignalEngine`. Long-only, exactly as deployed:
      BUY  -> target +1
      SELL -> target  0
      HOLD -> keep previous target

    We feed bars in order and hold state, mirroring the live event loop. EMAs are
    seeded from the first `warmup` closes so early bars don't emit noise.
    """

    def __init__(self, symbol: str, warmup: Optional[int] = None):
        from signal_engine import SignalEngine, Bar  # live modules, unchanged
        import config

        self._Bar = Bar
        self.symbol = symbol
        self.name = "Ripster baseline (live engine)"
        self._engine = SignalEngine(symbol)
        self._warmup = warmup if warmup is not None else config.EMA_WARMUP_BARS
        self._seeded = False
        self._pos = 0

    def _seed(self, bars: pd.DataFrame):
        closes = bars["close"].iloc[: self._warmup].tolist()
        self._engine.seed(closes)
        self._seeded = True

    def target_position(self, i: int, bars: pd.DataFrame,
                        features: Optional[pd.Series]) -> int:
        if not self._seeded:
            if len(bars) <= self._warmup:
                return 0
            self._seed(bars)
        if i < self._warmup:
            return 0

        row = bars.iloc[i]
        bar = self._Bar(
            symbol=self.symbol,
            timestamp=str(bars.index[i]),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]),
        )
        sig = self._engine.update(bar)
        if sig.action == "BUY":
            self._pos = 1
        elif sig.action == "SELL":
            self._pos = 0
        return self._pos


# ─────────────────────────────────────────────────────────────────────────────
#  A minimal VWAP baseline (the "already available from price/volume/VWAP" control)
# ─────────────────────────────────────────────────────────────────────────────
class VWAPTrend:
    """
    Control strategy using ONLY price/volume/VWAP — the information the mandate
    says BigShort must beat. Long when close > session VWAP and price is rising;
    flat otherwise. This is a benchmark, not the deployed strategy.
    """

    def __init__(self, symbol: str, lookback: int = 3):
        self.symbol = symbol
        self.name = "VWAP-trend control"
        self.lookback = lookback

    def target_position(self, i: int, bars: pd.DataFrame,
                        features: Optional[pd.Series]) -> int:
        if i < self.lookback:
            return 0
        row = bars.iloc[i]
        rising = row["close"] > bars.iloc[i - self.lookback]["close"]
        return 1 if (row["close"] > row["vwap"] and rising) else 0


# ─────────────────────────────────────────────────────────────────────────────
#  Overlay: baseline + one BigShort variable
# ─────────────────────────────────────────────────────────────────────────────
# An overlay function receives (base_target, feature_row) and returns the new
# target. feature_row is None when no captured BigShort data is aligned to the bar.
OverlayFn = Callable[[int, pd.DataFrame, Optional[pd.Series]], int]


class SignalOverlay:
    """
    Compose a base strategy with a filter/tilt derived from a captured feature.

    mode:
      "gate"  -> overlay may only VETO a base long (return 0), never create one.
                 Used to test "does variable X improve the baseline by filtering?"
      "tilt"  -> overlay may also open positions the base didn't (incl. shorts).
                 Used only in Phase 5+ once a variable has proven standalone value.

    The `decide` callable gets (base_target, feature_row) and returns a target.
    It must be look-ahead-free: feature_row is the point-in-time captured value
    for the CURRENT bar close, nothing later.
    """

    def __init__(self, base: Strategy, decide: Callable[[int, pd.Series | None], int],
                 name: str, mode: str = "gate"):
        self.base = base
        self.decide = decide
        self.name = name
        self.mode = mode

    def target_position(self, i: int, bars: pd.DataFrame,
                        features: Optional[pd.Series]) -> int:
        base_t = self.base.target_position(i, bars, features)
        new_t = self.decide(base_t, features)
        if self.mode == "gate":
            # Can only reduce conviction, never invent a trade the base didn't take.
            if base_t == 0:
                return 0
            if base_t > 0:
                return max(0, min(base_t, new_t))
            return min(0, max(base_t, new_t))
        return new_t
