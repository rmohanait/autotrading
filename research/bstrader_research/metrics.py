"""
metrics.py — performance metrics for the research backtester.

Every metric the mandate (Phase 2 / Phase 10) asks for is computed here from a
list of closed trades plus an equity curve. No external dependencies beyond the
standard library so this is trivially testable.

Definitions used (stated explicitly so results are unambiguous):
  win_rate       = winners / total closed trades
  profit_factor  = gross_profit / gross_loss   (inf if no losses)
  expectancy     = mean P&L per trade (in dollars) and in R-multiples if stops known
  avg_winner     = mean P&L of winning trades
  avg_loser      = mean P&L of losing trades (negative number)
  payoff_ratio   = |avg_winner / avg_loser|
  max_drawdown   = largest peak-to-trough decline of the equity curve (fraction)
  sharpe         = annualised Sharpe of per-bar or per-trade returns (stated which)
  CAGR / total_return computed from the equity curve.

All P&L figures are *net* of commissions and slippage — the backtester applies
costs before a trade reaches this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class Trade:
    """A single closed round-trip trade, net of costs."""
    symbol: str
    direction: str          # "long" or "short"
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    qty: float
    pnl: float              # net dollars, after commission + slippage
    pnl_pct: float          # net return on notional at entry
    r_multiple: Optional[float] = None   # pnl / initial risk, if a stop was set
    bars_held: int = 0
    entry_reason: str = ""
    exit_reason: str = ""
    tags: dict = field(default_factory=dict)   # e.g. {"gex_regime": "positive"}


# Trading days per year — used to annualise Sharpe from daily aggregates.
TRADING_DAYS = 252


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def max_drawdown(equity: Sequence[float]) -> float:
    """Largest peak-to-trough decline as a positive fraction (0.20 == -20%)."""
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def sharpe_ratio(returns: Sequence[float], periods_per_year: int = TRADING_DAYS,
                 rf: float = 0.0) -> float:
    """
    Annualised Sharpe from a series of *periodic* returns (e.g. daily).
    rf is the per-period risk-free rate (default 0). Returns 0 if undefined.
    """
    n = len(returns)
    if n < 2:
        return 0.0
    excess = [r - rf for r in returns]
    mean = sum(excess) / n
    var = sum((r - mean) ** 2 for r in excess) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return (mean / sd) * math.sqrt(periods_per_year)


def _daily_returns_from_equity(equity_by_day: Sequence[tuple[str, float]]) -> list[float]:
    """Given [(date, end_equity), ...] sorted by date, compute simple returns."""
    rets: list[float] = []
    prev = None
    for _, eq in equity_by_day:
        if prev is not None and prev > 0:
            rets.append((eq - prev) / prev)
        prev = eq
    return rets


def compute(trades: Sequence[Trade],
            equity_by_day: Optional[Sequence[tuple[str, float]]] = None,
            starting_equity: float = 100_000.0,
            n_days: Optional[int] = None) -> dict:
    """
    Compute the full metric suite.

    trades          : closed trades, net of costs.
    equity_by_day   : optional [(YYYY-MM-DD, end_of_day_equity), ...]. If given,
                      max drawdown and Sharpe use the equity curve; otherwise
                      they are approximated from the cumulative trade P&L path.
    starting_equity : account size assumption for return/CAGR figures.
    n_days          : number of trading days in the window (for trades/day).
    """
    closed = list(trades)
    total = len(closed)

    if total == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0, "expectancy_r": None,
            "avg_winner": 0.0, "avg_loser": 0.0, "payoff_ratio": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0, "net_pnl": 0.0,
            "max_drawdown": 0.0, "sharpe": 0.0, "total_return": 0.0,
            "trades_per_day": 0.0, "avg_trade": 0.0,
            "long_trades": 0, "short_trades": 0,
            "long_net_pnl": 0.0, "short_net_pnl": 0.0,
            "long_win_rate": 0.0, "short_win_rate": 0.0,
            "avg_bars_held": 0.0,
        }

    winners = [t for t in closed if t.pnl > 0]
    losers = [t for t in closed if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = abs(sum(t.pnl for t in losers))
    net_pnl = sum(t.pnl for t in closed)

    longs = [t for t in closed if t.direction == "long"]
    shorts = [t for t in closed if t.direction == "short"]

    r_vals = [t.r_multiple for t in closed if t.r_multiple is not None]

    # Equity curve for drawdown / Sharpe.
    if equity_by_day:
        eq_series = [eq for _, eq in equity_by_day]
        daily_rets = _daily_returns_from_equity(equity_by_day)
        end_equity = eq_series[-1] if eq_series else starting_equity
        dd_curve = eq_series
    else:
        # Reconstruct a per-trade equity path from cumulative net P&L.
        dd_curve = [starting_equity]
        for t in closed:
            dd_curve.append(dd_curve[-1] + t.pnl)
        end_equity = dd_curve[-1]
        # Per-trade returns as a coarse Sharpe input (labelled as such by caller).
        daily_rets = [t.pnl / starting_equity for t in closed]

    metrics = {
        "total_trades": total,
        "wins": len(winners),
        "losses": len(losers),
        "win_rate": _safe_div(len(winners), total),
        "profit_factor": (gross_profit / gross_loss) if gross_loss else float("inf"),
        "expectancy": net_pnl / total,                       # $ per trade
        "expectancy_r": (sum(r_vals) / len(r_vals)) if r_vals else None,
        "avg_winner": _safe_div(gross_profit, len(winners)),
        "avg_loser": _safe_div(sum(t.pnl for t in losers), len(losers)),
        "payoff_ratio": abs(_safe_div(
            _safe_div(gross_profit, len(winners)),
            _safe_div(sum(t.pnl for t in losers), len(losers)),
            default=0.0,
        )),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_pnl": net_pnl,
        "max_drawdown": max_drawdown(dd_curve),
        "sharpe": sharpe_ratio(daily_rets) if equity_by_day else sharpe_ratio(daily_rets, periods_per_year=total or 1),
        "total_return": _safe_div(end_equity - starting_equity, starting_equity),
        "avg_trade": net_pnl / total,
        "trades_per_day": _safe_div(total, n_days) if n_days else None,
        "long_trades": len(longs),
        "short_trades": len(shorts),
        "long_net_pnl": sum(t.pnl for t in longs),
        "short_net_pnl": sum(t.pnl for t in shorts),
        "long_win_rate": _safe_div(len([t for t in longs if t.pnl > 0]), len(longs)),
        "short_win_rate": _safe_div(len([t for t in shorts if t.pnl > 0]), len(shorts)),
        "avg_bars_held": _safe_div(sum(t.bars_held for t in closed), total),
    }
    return metrics


def format_report(name: str, m: dict) -> str:
    """Human-readable one-block summary of a metrics dict."""
    def pct(x):
        return f"{x*100:.1f}%" if x is not None else "n/a"

    def num(x, d=2):
        if x is None:
            return "n/a"
        if x == float("inf"):
            return "inf"
        return f"{x:.{d}f}"

    lines = [
        f"── {name} " + "─" * max(0, 40 - len(name)),
        f"  trades           : {m['total_trades']}  (long {m['long_trades']} / short {m['short_trades']})",
        f"  win rate         : {pct(m['win_rate'])}   ({m['wins']}W / {m['losses']}L)",
        f"  profit factor    : {num(m['profit_factor'])}",
        f"  expectancy/trade : ${num(m['expectancy'])}"
        + (f"   ({num(m['expectancy_r'])} R)" if m.get('expectancy_r') is not None else ""),
        f"  avg winner       : ${num(m['avg_winner'])}",
        f"  avg loser        : ${num(m['avg_loser'])}",
        f"  payoff ratio     : {num(m['payoff_ratio'])}",
        f"  net P&L          : ${num(m['net_pnl'])}",
        f"  max drawdown     : {pct(m['max_drawdown'])}",
        f"  sharpe           : {num(m['sharpe'])}",
        f"  total return     : {pct(m['total_return'])}",
        f"  trades/day       : {num(m['trades_per_day']) if m['trades_per_day'] is not None else 'n/a'}",
        f"  long net / short net : ${num(m['long_net_pnl'])} / ${num(m['short_net_pnl'])}",
    ]
    return "\n".join(lines)
