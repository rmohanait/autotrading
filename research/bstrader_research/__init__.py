"""
bstrader_research — a research-only harness for evaluating whether BigShort
order-flow / dealer-positioning data adds an out-of-sample edge to the existing
Ripster EMA-cloud SPY/QQQ intraday strategy.

This package is deliberately isolated from the live trading bot. Nothing here
imports the order/execution/notifier modules, and nothing here can place a
trade. The live bot is not modified by any of this code.

Modules
-------
data      : point-in-time OHLCV loaders (Alpaca / CSV / synthetic) + BigShort
            captured-feature loader.
strategy  : adapters that turn a bar stream into positions. The baseline reuses
            the live `signal_engine.py` so the backtest matches the real bot.
backtest  : event-driven, long/short-capable backtester with realistic costs
            and the existing session/risk restrictions.
metrics   : the full performance metric suite requested in the mandate.
splits    : chronological train/validation/test + walk-forward (never shuffles).
signals   : the BigShort feature interface consumed by Phases 3-9. Returns
            nothing until a real captured dataset exists — no fabricated values.
capture   : forward data-capture harness (schema + writer) — the only sound way
            to build a point-in-time BigShort dataset.
"""

__all__ = [
    "data",
    "strategy",
    "backtest",
    "metrics",
    "splits",
    "signals",
    "capture",
]

__version__ = "0.1.0"
