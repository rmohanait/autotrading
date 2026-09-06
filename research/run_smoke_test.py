#!/usr/bin/env python3
"""
run_smoke_test.py — prove the research harness runs end-to-end.

This uses SYNTHETIC random-walk data, not market data. Its ONLY purpose is to
show the pipeline is wired correctly: data -> live signal engine -> backtester
-> metrics -> chronological split. The numbers it prints are meaningless as a
trading result and must never be quoted as one.

Run:  python research/run_smoke_test.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bstrader_research import data, backtest, metrics, splits
from bstrader_research.strategy import RipsterBaseline, VWAPTrend


def main():
    print("=" * 68)
    print("SMOKE TEST — synthetic data, NOT a trading result")
    print("=" * 68)

    bars = data.make_synthetic_bars(days=40, seed=7)
    bars = data.regular_session_only(bars)
    bars["symbol"] = "SYN"
    print(f"Synthetic bars: {len(bars)} over {bars['session'].nunique()} sessions "
          f"({bars['session'].min()} .. {bars['session'].max()})")

    # Chronological split — proves no shuffling.
    sp = splits.train_val_test(bars)
    print(f"Split days -> train {sp.train['session'].nunique()}, "
          f"val {sp.val['session'].nunique()}, test {sp.test['session'].nunique()}")

    costs = backtest.CostModel(slippage_bps=1.0)
    session = backtest.SessionRules()  # defaults mirror the live config

    for label, strat_factory in [
        ("Ripster baseline (live engine)", lambda: RipsterBaseline("SYN")),
        ("VWAP-trend control", lambda: VWAPTrend("SYN")),
    ]:
        res = backtest.run(bars, strat_factory(), costs=costs, session=session,
                           position_size_pct=1.0)
        m = metrics.compute(res.trades, equity_by_day=res.equity_by_day,
                            starting_equity=res.starting_equity, n_days=res.n_days)
        print()
        print(metrics.format_report(label, m))

    print()
    print("If you see two metric blocks above, the pipeline is intact.")
    print("Next: run research/run_baseline.py with real SPY/QQQ data (Alpaca keys).")


if __name__ == "__main__":
    main()
