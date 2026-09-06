# BigShort edge research

A **research-only** harness to test, rigorously and out-of-sample, whether
BigShort order-flow / dealer-positioning data adds a real edge to the existing
Ripster EMA-cloud SPY/QQQ intraday strategy — **without touching the live bot.**

> **Read first:** [`bigshort_data_access.md`](bigshort_data_access.md) — the
> gating Phase-1 finding. Short version: BigShort has **no API/export**, its
> history is **~2 years (not 6)**, its indicator **definitions have drifted**, and
> its historical values **look-ahead/repaint**. The only sound dataset is one you
> **capture forward** — which is why this harness is built around capture, not
> scraping history. **Do not buy a full subscription before running the repaint
> audit on the $37 trial.**

## Two findings that reframe the mandate

1. **There was no backtesting framework in this repo.** The existing code is a
   *live* Ripster paper-trading bot (Alpaca websocket → signal engine → orders →
   SMS/SQLite/dashboard). It had no historical backtest, no train/test machinery,
   and no metric computation from history. The mandate assumed one existed; it
   didn't. This `research/` package is that missing framework — and it **reuses the
   live `signal_engine.py` unchanged**, so the baseline measures the real strategy.

2. **BigShort history is not testable as-is** (see the data-access doc). So the
   plan's order is: build the baseline + a forward-capture pipeline now; the
   BigShort phases produce numbers only after enough forward data accumulates. No
   BigShort performance figure is fabricated anywhere in this code.

## Layout

```
research/
  bigshort_data_access.md     Phase 1 answer (the gating question)
  evaluation_protocol.md      Pre-registered Phases 3–10 (anti-overfitting rules)
  requirements-research.txt   Deps (separate from the live bot)
  run_baseline.py             Phase 2: baseline on real SPY/QQQ (needs Alpaca keys)
  run_smoke_test.py           End-to-end proof on synthetic data (no keys needed)
  capture_bigshort.py         Forward 5-min capture (manual or Playwright reader)
  bstrader_research/
    data.py       OHLCV loaders (Alpaca/CSV/synthetic) + captured-feature loader + VWAP
    strategy.py   RipsterBaseline (reuses live engine), VWAPTrend control, SignalOverlay
    backtest.py   Event-driven, next-open fills (no look-ahead), costs, session/risk rules
    metrics.py    Full metric suite (PF, expectancy, DD, Sharpe, long/short, …)
    splits.py     Chronological train/val/test + walk-forward (never shuffles)
    signals.py    BigShort feature registry + forward-return IC tests (Phase 3 core)
    capture.py    Capture schema/writer + repaint audit + point-in-time compaction
  tests/test_harness.py       Fast unit tests (no network/browser/clock waits)
```

## Quick start

```bash
pip install -r research/requirements-research.txt

# 1) Prove the pipeline works (synthetic data — NOT a trading result):
python research/run_smoke_test.py

# 2) Unit tests:
python research/tests/test_harness.py

# 3) Baseline on real data (needs ALPACA_API_KEY/SECRET in env or repo-root .env):
python research/run_baseline.py --symbols SPY QQQ --days 720 --feed sip

# 4) Start building the BigShort dataset (during the $37 trial):
python research/capture_bigshort.py --symbol SPY --mode manual
#    ...later, audit repaint and compact to point-in-time parquet:
python -c "from research.bstrader_research import capture; \
           print(capture.analyze_repaint('SPY')); \
           print(capture.CaptureWriter('SPY').compact_to_parquet())"
```

## Guarantees this harness enforces

- **No look-ahead:** signals decided on bar *i*'s close are executed at bar
  *i+1*'s open; forward returns never cross a session boundary; captured features
  are stamped with read-time and only `revision 0` (first live reading) feeds the
  backtest.
- **No shuffling** of time series, ever.
- **Costs always applied.**
- **No fabricated BigShort values:** with no captured file, the loader raises.
- **Live bot untouched:** nothing here imports order/execution/notifier code, and
  nothing here can place a trade.

## The bar BigShort must clear

Beat the existing Ripster baseline **and** the price/volume/VWAP control **and**
(where feasible) a self-computed GEX, on the **test fold**, in **both** SPY and
QQQ, at realistic cost, surviving multiple-hypothesis correction. If it doesn't,
the correct answer is "no material edge" — which saves the subscription cost. See
`evaluation_protocol.md` for the full decision rules and the KEEP/FILTER/LEVEL/
REJECT rubric.
