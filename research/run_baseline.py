#!/usr/bin/env python3
"""
run_baseline.py — Phase 2: establish the existing strategy's baseline on SPY/QQQ.

Pulls real 5-minute bars from Alpaca, runs the LIVE Ripster engine (unchanged)
plus the VWAP-trend control, and reports the full metric suite over the whole
sample AND over a chronological train/val/test split. No BigShort data is
involved — this is the yardstick everything else must beat.

Prerequisites (on your machine, not this sandbox):
  * ALPACA_API_KEY / ALPACA_SECRET_KEY in the environment or a .env at repo root.
  * pip install -r research/requirements-research.txt
  * For a real 2-year study use the SIP feed (paid Alpaca data plan): pass
    --feed sip. The free IEX feed works but its volume is only IEX prints.

Usage:
  python research/run_baseline.py --symbols SPY QQQ --days 720 --feed sip
  python research/run_baseline.py --symbols SPY QQQ --days 120 --feed iex
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bstrader_research import data, backtest, metrics, splits
from bstrader_research.strategy import RipsterBaseline, VWAPTrend


def _load_dotenv():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    except Exception:
        pass


def evaluate(bars, symbol, strat_factory, label, costs, session, size_pct):
    res = backtest.run(bars, strat_factory(), costs=costs, session=session,
                       position_size_pct=size_pct)
    m = metrics.compute(res.trades, equity_by_day=res.equity_by_day,
                        starting_equity=res.starting_equity, n_days=res.n_days)
    print(metrics.format_report(f"{symbol} — {label}", m))
    print()
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["SPY", "QQQ"])
    ap.add_argument("--days", type=int, default=720)
    ap.add_argument("--feed", default="iex", choices=["iex", "sip"])
    ap.add_argument("--timeframe", default="5Min")
    ap.add_argument("--slippage-bps", type=float, default=1.0)
    ap.add_argument("--size-pct", type=float, default=1.0,
                    help="fraction of equity per trade (1.0 = fully invested per trade)")
    ap.add_argument("--csv", default=None,
                    help="load bars from this CSV instead of Alpaca (columns: timestamp,ohlcv)")
    args = ap.parse_args()

    _load_dotenv()
    costs = backtest.CostModel(slippage_bps=args.slippage_bps)
    session = backtest.SessionRules.from_live_config()

    for symbol in args.symbols:
        print("=" * 68)
        print(f"BASELINE — {symbol}  (feed={args.feed}, days={args.days})")
        print("=" * 68)
        if args.csv:
            bars = data.load_csv_bars(args.csv)
        else:
            bars = data.load_alpaca_bars(symbol, days=args.days,
                                         timeframe=args.timeframe, feed=args.feed)
        bars = data.regular_session_only(bars)
        bars["symbol"] = symbol
        print(f"{len(bars)} bars over {bars['session'].nunique()} sessions "
              f"({bars['session'].min()} .. {bars['session'].max()})\n")

        # Whole-sample
        evaluate(bars, symbol, lambda: RipsterBaseline(symbol),
                 "Ripster baseline [full sample]", costs, session, args.size_pct)
        evaluate(bars, symbol, lambda: VWAPTrend(symbol),
                 "VWAP control [full sample]", costs, session, args.size_pct)

        # Chronological split — report the TEST fold prominently (that's the OOS number).
        sp = splits.train_val_test(bars)
        for fold_name, fold in [("train", sp.train), ("val", sp.val), ("test", sp.test)]:
            if fold.empty:
                continue
            fold = fold.copy()
            fold["symbol"] = symbol
            evaluate(fold, symbol, lambda: RipsterBaseline(symbol),
                     f"Ripster baseline [{fold_name}]", costs, session, args.size_pct)

    print("Baseline complete. Record these numbers — every BigShort test is judged")
    print("against the [test] fold OOS figures, not the full-sample ones.")


if __name__ == "__main__":
    main()
