"""
backtest.py — event-driven backtester for the research harness.

Design choices that keep results honest:

  * No look-ahead. The target position for bar i is computed from information up
    to and including bar i's CLOSE, then executed at bar i+1's OPEN. You never
    get filled at the price that triggered your own signal.

  * Realistic frictions. Commission (per share and/or per trade) plus slippage
    (in basis points of price) are charged on every entry and exit. Defaults are
    deliberately non-zero; a strategy that only works at zero cost is not real.

  * Session + risk rules mirror the live bot's config: skip the first
    SKIP_OPEN_MINUTES, force-flat before MARKET_CLOSE, cap MAX_TRADES_PER_DAY.
    These are ON by default so the baseline matches what actually runs; they can
    be relaxed per the mandate's "unless there is a compelling reason" clause.

  * Long/short capable, but the Ripster baseline only ever targets +1/0, so the
    baseline stays long-only exactly as deployed.

The output is a BacktestResult carrying closed Trades (net of costs) and a daily
equity curve, ready for metrics.compute().
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .metrics import Trade

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


@dataclass
class CostModel:
    """Transaction cost assumptions, charged on both entry and exit."""
    commission_per_share: float = 0.0     # Alpaca is commission-free; keep 0 unless modelling a broker
    commission_per_trade: float = 0.0
    slippage_bps: float = 1.0             # 1 bp = 0.01% of price, each side
    # Effective per-share slippage = price * slippage_bps/1e4.


@dataclass
class SessionRules:
    """Intraday session / risk restrictions (defaults mirror config.py)."""
    skip_open_minutes: int = 15
    close_hour: int = 15
    close_minute: int = 45
    open_hour: int = 9
    open_minute: int = 30
    max_trades_per_day: Optional[int] = 3
    force_flat_eod: bool = True

    @classmethod
    def from_live_config(cls) -> "SessionRules":
        import config
        return cls(
            skip_open_minutes=config.SKIP_OPEN_MINUTES,
            close_hour=config.MARKET_CLOSE_HOUR,
            close_minute=config.MARKET_CLOSE_MIN,
            open_hour=config.MARKET_OPEN_HOUR,
            open_minute=config.MARKET_OPEN_MIN,
            max_trades_per_day=config.MAX_TRADES_PER_DAY,
            force_flat_eod=True,
        )


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    equity_by_day: list[tuple[str, float]] = field(default_factory=list)
    n_days: int = 0
    starting_equity: float = 100_000.0
    bars: int = 0


def _minutes_et(ts_et: pd.Timestamp) -> int:
    return ts_et.hour * 60 + ts_et.minute


def run(
    bars: pd.DataFrame,
    strategy,
    features: Optional[pd.DataFrame] = None,
    starting_equity: float = 100_000.0,
    position_size_pct: float = 1.0,
    costs: Optional[CostModel] = None,
    session: Optional[SessionRules] = None,
) -> BacktestResult:
    """
    Run `strategy` over `bars`.

    position_size_pct : fraction of equity deployed per trade (1.0 == fully
                        invested; the live bot uses 0.05, but for measuring a
                        signal's edge fully-invested per-trade returns are the
                        cleaner unit — set to match the live bot for a dollar-P&L
                        view).
    features          : optional aligned BigShort feature frame (same index as
                        bars). Passed row-by-row to the strategy.
    """
    costs = costs or CostModel()
    session = session or SessionRules()

    n = len(bars)
    if n < 2:
        return BacktestResult(getattr(strategy, "name", "?"),
                              bars.get("symbol", ["?"])[0] if "symbol" in bars else "?",
                              starting_equity=starting_equity)

    et = bars["et"]
    sessions = bars["session"].tolist()
    opens = bars["open"].tolist()
    closes = bars["close"].tolist()
    idx = bars.index

    # Precompute allowed-to-trade mask per bar from session rules.
    open_min = session.open_hour * 60 + session.open_minute
    close_min = session.close_hour * 60 + session.close_minute
    allow_new = []
    force_flat = []
    for ts in et:
        m = _minutes_et(ts)
        allow_new.append(open_min + session.skip_open_minutes <= m < close_min)
        force_flat.append(m >= close_min)

    position = 0                 # current signed units of "1 lot"
    entry_price = 0.0
    entry_time = ""
    entry_bar = 0
    entry_reason = ""
    trades: list[Trade] = []
    trades_today = 0
    cur_session = sessions[0]

    equity = starting_equity
    realized = 0.0
    equity_by_day: list[tuple[str, float]] = []

    def feat_row(i: int) -> Optional[pd.Series]:
        if features is None:
            return None
        ts = idx[i]
        if ts in features.index:
            return features.loc[ts]
        return None

    def _fill_price(ref: float, side: int) -> float:
        """Apply slippage: buys fill higher, sells fill lower."""
        slip = ref * costs.slippage_bps / 1e4
        return ref + side * slip

    def _charge(qty: float) -> float:
        return abs(qty) * costs.commission_per_share + costs.commission_per_trade

    def _close_position(exit_ref: float, exit_time: str, reason: str):
        nonlocal position, realized, equity, entry_price, entry_bar, entry_reason
        side = -1 if position > 0 else 1   # selling to close a long, buying to close a short
        fill = _fill_price(exit_ref, side)
        qty = abs(position_units)
        direction = "long" if position > 0 else "short"
        gross = (fill - entry_price) * qty * (1 if position > 0 else -1)
        commission = _charge(qty) + entry_commission
        pnl = gross - commission
        notional = entry_price * qty
        trades.append(Trade(
            symbol=sym, direction=direction,
            entry_time=entry_time, exit_time=exit_time,
            entry_price=entry_price, exit_price=fill, qty=qty,
            pnl=pnl, pnl_pct=(pnl / notional if notional else 0.0),
            bars_held=(exit_bar_ref[0] - entry_bar),
            entry_reason=entry_reason, exit_reason=reason,
            tags={"session": cur_session},
        ))
        realized += pnl
        equity = starting_equity + realized
        position = 0

    sym = str(bars["symbol"].iloc[0]) if "symbol" in bars.columns else getattr(strategy, "symbol", "?")
    position_units = 0.0
    entry_commission = 0.0
    exit_bar_ref = [0]

    # Main loop. Decide on bar i (from its close); act at bar i+1's open.
    pending_target = 0
    for i in range(n):
        # New session bookkeeping.
        if sessions[i] != cur_session:
            cur_session = sessions[i]
            trades_today = 0

        # 1) Execute yesterday's/previous bar's decision at THIS bar's open.
        if i > 0:
            exec_open = opens[i]
            # Force-flat near close overrides any target.
            desired = pending_target
            if force_flat[i]:
                desired = 0

            if desired != position:
                # Close existing position first if flipping or flattening.
                if position != 0 and (desired == 0 or (desired > 0) != (position > 0)):
                    exit_bar_ref[0] = i
                    _close_position(exec_open, str(idx[i]),
                                    "eod flat" if force_flat[i] else "signal exit")
                # Open a new position if desired and allowed.
                if desired != 0 and position == 0 and allow_new[i] \
                        and (session.max_trades_per_day is None or trades_today < session.max_trades_per_day):
                    side = 1 if desired > 0 else -1
                    fill = _fill_price(exec_open, side)
                    # Size: deploy position_size_pct of current equity.
                    dollars = (starting_equity + realized) * position_size_pct
                    position_units = max(1.0, dollars / fill) if fill > 0 else 0.0
                    entry_price = fill
                    entry_time = str(idx[i])
                    entry_bar = i
                    entry_commission = _charge(position_units)
                    entry_reason = getattr(strategy, "name", "")
                    position = side
                    trades_today += 1

        # 2) Decide target from bar i's close (used at bar i+1's open).
        pending_target = strategy.target_position(i, bars, feat_row(i))

        # 3) End-of-day equity snapshot.
        is_last_of_day = (i == n - 1) or (sessions[i + 1] != cur_session)
        if is_last_of_day:
            # Mark-to-market open position at this bar's close.
            mtm = 0.0
            if position != 0:
                mtm = (closes[i] - entry_price) * position_units * (1 if position > 0 else -1)
            equity_by_day.append((cur_session, starting_equity + realized + mtm))

    # Close any dangling position at final close.
    if position != 0:
        exit_bar_ref[0] = n - 1
        _close_position(closes[-1], str(idx[-1]), "end of data")

    n_days = len(set(sessions))
    return BacktestResult(
        strategy_name=getattr(strategy, "name", "?"),
        symbol=sym,
        trades=trades,
        equity_by_day=equity_by_day,
        n_days=n_days,
        starting_equity=starting_equity,
        bars=n,
    )
