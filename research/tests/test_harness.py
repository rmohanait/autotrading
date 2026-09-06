"""
Fast unit tests for the research harness — no network, no browser, no clock waits.
Run:  python -m pytest research/tests/ -q     (or)     python research/tests/test_harness.py
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bstrader_research import metrics, capture, signals, data, backtest, splits
from bstrader_research.strategy import RipsterBaseline


def test_metrics_basic():
    trades = [
        metrics.Trade("SPY", "long", "t0", "t1", 100, 102, 10, pnl=20, pnl_pct=0.02, bars_held=3),
        metrics.Trade("SPY", "long", "t2", "t3", 100, 99, 10, pnl=-10, pnl_pct=-0.01, bars_held=2),
        metrics.Trade("SPY", "long", "t4", "t5", 100, 104, 10, pnl=40, pnl_pct=0.04, bars_held=5),
    ]
    m = metrics.compute(trades, starting_equity=1000, n_days=2)
    assert m["total_trades"] == 3
    assert m["wins"] == 2 and m["losses"] == 1
    assert abs(m["win_rate"] - 2/3) < 1e-9
    assert abs(m["profit_factor"] - (60/10)) < 1e-9      # gross 60 / loss 10
    assert abs(m["expectancy"] - (50/3)) < 1e-9
    assert m["trades_per_day"] == 1.5
    print("test_metrics_basic OK")


def test_max_drawdown():
    eq = [100, 120, 90, 130, 60]
    dd = metrics.max_drawdown(eq)
    assert abs(dd - (130 - 60)/130) < 1e-9                # deepest trough from peak 130
    print("test_max_drawdown OK")


def test_capture_no_lookahead_and_revisions(tmp_path=None):
    import tempfile
    d = tempfile.mkdtemp()
    w = capture.CaptureWriter("TST", out_dir=d)

    # Fake clock that jumps to fixed instants; fake reader returns changing values.
    instants = iter([
        datetime(2025, 1, 6, 15, 2, tzinfo=timezone.utc),   # now -> boundary 15:05
        datetime(2025, 1, 6, 15, 5, tzinfo=timezone.utc),   # capture_ts
        datetime(2025, 1, 6, 15, 5, tzinfo=timezone.utc),   # loop check
    ])
    reads = iter([{"price": 450.0, "nof": 1.2, "gex": -3.0}])

    now_state = {"t": datetime(2025, 1, 6, 15, 2, tzinfo=timezone.utc)}
    def now_fn():
        return now_state["t"]
    def sleep_fn(secs):
        # advance the clock to the boundary instead of really sleeping
        now_state["t"] = datetime(2025, 1, 6, 15, 5, tzinfo=timezone.utc)

    capture.run_capture_loop("TST", lambda s: next(reads), writer=w,
                             max_snapshots=1, sleep_fn=sleep_fn, now_fn=now_fn)

    import csv
    with open(w.csv_path) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    r = rows[0]
    assert r["bar_ts"].startswith("2025-01-06T15:05")
    # capture_ts must be >= bar_ts (we read AT/after the bucket close -> no look-ahead)
    assert r["capture_ts"] >= r["bar_ts"]
    assert r["revision"] == "0"
    assert float(r["nof"]) == 1.2
    print("test_capture_no_lookahead_and_revisions OK")


def test_load_bigshort_features_raises_without_data():
    try:
        data.load_bigshort_features("NOPE", path="/nonexistent/x.parquet")
        assert False, "should have raised"
    except FileNotFoundError:
        print("test_load_bigshort_features_raises_without_data OK")


def test_forward_returns_no_cross_session():
    bars = data.make_synthetic_bars(days=3, seed=1)
    bars = data.regular_session_only(bars)
    fwd = signals.forward_returns(bars, horizons=(1, 6))
    # last bar of each session must have NaN forward returns (no leak into next day)
    last_idx = bars.groupby("session").tail(1).index
    assert fwd.loc[last_idx, "fwd_1"].isna().all()
    assert fwd.loc[last_idx, "fwd_6"].isna().all()
    print("test_forward_returns_no_cross_session OK")


def test_baseline_runs_and_is_long_only():
    bars = data.make_synthetic_bars(days=15, seed=3)
    bars = data.regular_session_only(bars)
    bars["symbol"] = "SYN"
    res = backtest.run(bars, RipsterBaseline("SYN"), position_size_pct=1.0)
    assert all(t.direction == "long" for t in res.trades)   # deployed strategy is long-only
    m = metrics.compute(res.trades, equity_by_day=res.equity_by_day, n_days=res.n_days)
    assert m["short_trades"] == 0
    print(f"test_baseline_runs_and_is_long_only OK ({m['total_trades']} trades)")


if __name__ == "__main__":
    test_metrics_basic()
    test_max_drawdown()
    test_capture_no_lookahead_and_revisions()
    test_load_bigshort_features_raises_without_data()
    test_forward_returns_no_cross_session()
    test_baseline_runs_and_is_long_only()
    print("\nAll harness tests passed.")
