# Pre-registered evaluation protocol (Phases 3–10)

This protocol is written **before any BigShort data exists**, deliberately. The
research question is not "can I find a BigShort combination that looks good on a
chart?" — you always can. It is:

> **Does BigShort data materially improve the risk-adjusted, out-of-sample
> performance of the existing SPY/QQQ intraday system, versus information already
> available from price, volume and VWAP?**

Pre-registering the tests, horizons, thresholds-policy, and decision rules is what
stops that question from degrading into a fishing expedition. Deviations from this
document must be written down as deviations.

---

## 0. Ground rules (apply to every phase)

1. **Chronological only.** Never shuffle intraday data. Use `splits.train_val_test`
   (60/20/20 by day) and `splits.walk_forward`. The **test fold is touched last
   and once**; report it, don't optimize on it.
2. **Point-in-time inputs only.** BigShort features come from `revision 0` live
   captures (`capture.compact_to_parquet`). Any field that fails the repaint audit
   (`capture.analyze_repaint`) is disqualified from *entry-signal* use and may be
   used only as context/level, clearly labelled.
3. **Costs always on.** Every backtest includes commission + slippage
   (`backtest.CostModel`, default 1 bp/side; stress at 2–3 bp). A result that dies
   at realistic cost is a non-result.
4. **The bar to clear is the control, not zero.** A BigShort variable must beat
   (a) the existing Ripster baseline **and** (b) the `VWAPTrend` price/volume/VWAP
   control **and** (c) where feasible, a self-computed GEX. Beating buy-and-hold
   is necessary but not sufficient.
5. **Multiple-hypothesis discipline.** Count every variable × transform × horizon
   × symbol test. Apply **Benjamini–Hochberg FDR at q = 0.10** across the family
   of Phase-3 tests before calling any IC "significant." A raw p < 0.05 among 200
   tests is noise.
6. **Two-symbol, multi-regime persistence.** An effect must show in **both SPY and
   QQQ**, and survive across at least two of {2023, 2024, 2025 partial}, and across
   trend days vs range days (classified by that day's close-to-open range / ATR).
   Effects that appear in one symbol or one year are rejected.
7. **Effect size, not just significance.** Report IC and OOS **profit factor /
   expectancy** with bootstrap confidence intervals. Prefer the lower CI bound when
   ranking. Win rate is explicitly *not* the objective.

---

## Phase 2 — Baseline (runnable now, no BigShort)

Run `research/run_baseline.py --symbols SPY QQQ --days 720 --feed sip`.
Record, per symbol, for full-sample and for train/val/**test**:
trades, win rate, profit factor, expectancy ($ and R), avg winner, avg loser,
payoff, max drawdown, Sharpe, trades/day, total return, long vs short.

Freeze the **test-fold** figures as the numbers every later phase is compared to.
Also record the `VWAPTrend` control on the same folds — that is the "already
available from price/volume/VWAP" yardstick.

> Status: framework complete and smoke-tested; awaiting a run with your Alpaca
> keys + SIP data (the sandbox that built this cannot reach market data).

## Phase 3 — Each variable's predictive information (one at a time)

For every feature in `signals.REGISTRY`, and for each transform
{level, slope (diff), z-score, divergence-vs-price, and for `momoflow` also the
**inverse**}, compute `signals.predictive_information` at horizons **5/10/15/30/60
min** for SPY and QQQ:
- Spearman **IC** vs forward return,
- **circular-shift p-value** (preserves autocorrelation),
- sample size.

Apply BH-FDR (rule 5). A variable/transform "carries information" only if it is
FDR-significant **and** the sign is consistent across horizons **and** it holds in
both symbols. Everything else is a candidate **REJECT**.

**MomoFlow is tested both directly and inversely and the data decides** — do not
assume rising MomoFlow is bullish; BigShort documents it as emotional/"dumb money"
flow, so the inverse (fade) reading is a first-class hypothesis.

## Phase 4 — GEX regime hypothesis

Split all bars by GEX regime (positive / negative / flip). Test:
- realized 30-min volatility and |return| in positive vs negative GEX (Mann–Whitney);
- **trend/breakout** strategy PF/expectancy in each regime;
- **mean-reversion** strategy PF/expectancy in each regime.

Hypothesis (to be tested, not assumed): positive GEX → mean-reverting/lower move;
negative GEX → trending/higher move. If regimes separate the two strategy families
with a meaningful, OOS-stable gap, GEX earns a **FILTER** classification (regime
selector), **not** an entry signal.

## Phase 5 — Flow combination (only variables that passed Phase 3)

Build candidates like: `price > VWAP AND NOF rising AND FastFlow confirms`, GEX as
regime filter, tested with `strategy.SignalOverlay`. **Thresholds are fit on the
train fold only** (e.g. NOF slope quantiles), validated on val, reported on test.
Test whether SmartFlow divergence adds to the setup. Never hand-tune thresholds to
make the equity curve pretty — thresholds come from train-fold distributions.

## Phase 6 — Hedge points vs random control levels

For Gravity/Call/Put HP and dark-pool levels, measure conditional outcomes
(P(reach), median time-to-reach, continuation vs rejection on approach) and
**compare against matched random/control levels** at the same distance and time of
day. The effect is real only if it beats the control-level distribution. Stratify
by GEX regime and flow direction. Passing → **LEVEL** classification.

## Phase 7 — Divergences

Detect price vs NOF/SmartFlow divergences (price lower-low while flow higher-low,
and the mirror). Quantify forward 5–60 min outcomes vs base rate. Require the same
persistence rules. This is high-value but high-degrees-of-freedom, so the
divergence-detection parameters are fixed in advance (swing lookback = 6 bars,
min swing = 0.15%) and not tuned per result.

## Phase 8 — Best combined model

Only variables that earned KEEP/FILTER/LEVEL enter. Start simple; add one term at a
time; keep a term only if it improves the **test-fold lower-CI profit factor**.
Compare final system against: A) existing strategy, B) existing + BigShort filter,
C) BigShort-only, D) buy-and-hold/control. Walk-forward the final model.

## Phase 9 — Overfitting guards (continuous, not a final step)

- Walk-forward everything (`splits.walk_forward`, 60/20 default).
- Report the **deflated** view: how many configurations were tried, and the BH-FDR
  survivors. Prefer few, simple, cross-symbol-stable relationships.
- A configuration that needs >2 tuned thresholds is treated as suspect by default.
- Sensitivity: re-run at 2–3 bp slippage and ±1 bar execution lag; edges that
  vanish are not edges.

## Phase 10 — Deliverable

Produce the incremental table (the harness's `report` output will assemble this
once data exists):

```
Baseline → +NOF → +FastFlow → +GEX(filter) → +SmartFlow → +HP → best combo
```
with ΔWinRate, ΔProfitFactor, ΔExpectancy, ΔMaxDD, Δtrades, Δavg-trade, and the
**OOS (test-fold)** value for each step, for SPY and QQQ.

Then classify **every** BigShort signal:

| Class | Meaning | Bar to clear |
|---|---|---|
| **KEEP** | measurable independent edge | FDR-significant IC + OOS PF improvement over control, both symbols |
| **FILTER** | useful only for regime/context | separates strategy families OOS (e.g. GEX) but no standalone entry edge |
| **LEVEL** | useful for targets/S-R | beats matched random levels OOS |
| **REJECT** | no incremental edge | fails the above; dropped regardless of marketing |

The default prior for every signal is **REJECT**; it is promoted only by evidence
that survives the guards above.

---

### Decision on the core question
Ship a BigShort-augmented change to the (still-separate) research strategy **only
if** at least one signal reaches KEEP or a FILTER/LEVEL combination raises the
**test-fold profit factor and expectancy** over the baseline **and** the control,
in **both** SPY and QQQ, at realistic cost. Otherwise the honest answer is "no
material edge" — and that is a valid, money-saving result. The live bot is not
touched either way until this bar is met.
