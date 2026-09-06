# Phase 1 — Can BigShort data even be tested? (The gating question)

**Bottom line up front:** Do **not** buy a full BigShort subscription expecting a
ready-made research dataset. BigShort has **no public API and no data export**,
its history is **~2 years, not six**, its indicator **definitions have changed
over time**, and its historical chart values are **recomputed with full-session
information** (look-ahead). A defensible study is only possible from **forward
data you capture yourself**, 5 minutes at a time. The harness in this branch is
built for exactly that.

This document answers the mandate's Phase 1 before any money is spent. Sources
are listed at the end. Note: `bigshort.com` and `help.bigshort.com` are blocked
by this environment's network egress proxy, so the findings below come from web
search over their public help-center/FAQ/pricing pages plus their blog; verify
the live specifics against your own logged-in account before relying on them.

---

## 1. Is there an API or data export? — No (as far as is publicly documented)

- BigShort runs as a **browser app at `app.bigshort.com`**. All documentation
  describes interacting with **charts in the browser**; nothing in the help
  center, FAQ, product page, or pricing page describes a REST/websocket **API**,
  a **CSV/Excel export**, or any programmatic access to indicator values.
- The **Similarity Search / "Historical"** feature lets you *view* past sessions
  on the chart. Viewing is not the same as obtaining a timestamped series you can
  join to price bars.
- Conclusion: **you cannot download NOF/GEX/FastFlow/etc. as a table.** Any
  dataset must be read off the rendered chart.

**Implication for the harness:** `data.load_bigshort_features()` has *no*
historical download path on purpose. It reads only a dataset you captured
forward (`capture.py`). With no captured file it raises — it never invents values.

## 2. How much history, at what resolution? — ~2 years intraday

- BigShort states it holds historical data "for the last **2 years**" per ticker,
  and Similarity Search "scans years of intraday data."
- The mandate's premise of **"six years of 5-minute history"** appears **incorrect**
  — plan for ~2 years. Two years of RTH 5-minute SPY/QQQ bars is still ~196k
  bars/ticker, which is enough for a study *if* the values were trustworthy
  point-in-time. They are not (see §4).

## 3. Definition drift — the indicators are not the same across the history

This is the finding that most threatens a historical backtest, and it is easy to
miss:

- **"FastFlow was formerly called SmartFlow."** The name *SmartFlow* now refers to
  a *different* (institutional-flow) indicator, and there are additional **Elite**
  indicators **SmartFlow 3 (SF3)** and **SmartFlow 3.1 (SF3.1)** that are
  separate again.
- So a 2-year "SmartFlow" series is almost certainly **not one consistent signal**
  — its meaning changed underneath the label. Backtesting across a renamed/
  redefined indicator silently mixes two different variables.

**Implication:** capture the indicator *you actually subscribe to today*, going
forward, under a fixed definition. Do not trust a long historical series of any
flow indicator whose definition may have changed. The feature registry in
`signals.py` flags `fastflow`/`smartflow` with this warning.

## 4. Do the values repaint / get revised? — Structurally, yes for the dealer-positioning set

"Repaint" = a value shown for a *past, closed* bar changes later when more data
arrives. For the BigShort family this is not a fringe risk; it is inherent to how
several of the signals are constructed:

- **GEX, Gravity HP, Call HP, Put HP** are computed from the **current options
  open-interest / gamma surface**. That surface is revised throughout the day (and
  OI is finalized after the close). A hedge-point line you see drawn across
  *earlier* times on today's chart reflects **later** information — textbook
  look-ahead if you scrape it from history.
- **NOFA** is an **intraday accumulation** ("running total"); the shape of the
  early curve as rendered now can reflect end-of-day normalization.
- Order-flow reconstructions (**NOF, FastFlow, MomoFlow, SmartFlow**) can also be
  smoothed/revised as trades are classified.
- Industry context: >95% of charting indicators repaint to some degree; BigShort
  publishes no explicit "non-repainting" guarantee for these fields.

**You cannot verify repaint from the outside** — it requires watching the live
value at bar close and comparing it to what the chart shows for that same bar
later. The capture harness is designed to *measure* this: every re-read of a
bucket is stored with an incrementing `revision`, and `capture.analyze_repaint()`
reports any field whose later value differs from the first live reading. **Run
this repaint audit before trusting any BigShort field**, and only ever feed the
**first live reading** (`revision 0`) into the backtest (`compact_to_parquet()`
enforces this).

## 5. Pricing (verify on the pricing page before purchase)

- A **2-week trial for ~$37** is advertised — enough to run the **repaint audit**
  (§4) and a **capture pilot** before committing.
- Tiers referenced: **Basic / Pro / Elite** (Elite adds SF3/SF3.1 etc.); Pro
  billed annually is discounted (~26% off was mentioned). A **professional data
  license** is required for commercial/institutional use of the live exchange data.
- Exact monthly numbers were not reliably visible in search — confirm at
  `bigshort.com/pricing`.

**Recommended spend sequence:**
1. Take the **$37 two-week trial**.
2. Day 1–2: run `capture_bigshort.py` in `manual` mode a few times and run the
   **repaint audit**. If the dealer-positioning fields repaint materially, treat
   them as **context/levels only**, never as historically-backtestable entry
   signals.
3. If (and only if) capture is clean and stable, subscribe to the tier that
   carries the specific indicators you'll capture, and let the forward dataset
   accumulate (aim for ≥3–6 months across mixed regimes before drawing OOS
   conclusions).

---

## 6. What this means for the research plan

| Question | Answer | Consequence |
|---|---|---|
| Bulk historical export? | No | Must capture forward. |
| API? | None documented | Scrape rendered values (Playwright) or manual. |
| History length | ~2 yrs (not 6) | Fine *if* point-in-time — but it isn't. |
| Definitions stable? | No (SmartFlow→FastFlow, SF3/3.1) | Long historical flow series untrustworthy. |
| Repaint? | Likely for GEX/HP/NOFA by construction | Only live-captured `revision 0` is usable. |
| Look-ahead-free history obtainable? | **No** | **Forward capture is mandatory.** |

Therefore Phases 3–9 (predictive tests, GEX regime, hedge points, divergences,
combined model) **cannot run on scraped history**. They run on the **captured
forward dataset**, and the harness is already wired to consume exactly that
(`data.load_bigshort_features` → `signals.predictive_information` → the overlay
backtests). Until enough forward data exists, those phases produce *no numbers*,
and this study will not manufacture any.

## 7. A cheaper cross-check worth doing in parallel

Some of what BigShort sells is reconstructable from primary sources you can
license with real history and no repaint ambiguity, which lets you sanity-check
whether BigShort's *packaging* adds anything:

- **Dealer GEX / gamma** from options chains (e.g. via a historical options data
  vendor) — you can compute a transparent, non-repainting GEX yourself.
- **Options flow / sweeps** from a flow vendor with timestamped history.
- **Dark-pool / off-exchange prints** from consolidated tape vendors.

If a self-computed GEX regime already captures most of the edge, BigShort's GEX
adds little. This is the honest control for the whole exercise and is why
`signals.py` treats "beat price/volume/VWAP **and** a self-computable GEX" as the
bar to clear.

---

### Sources
- BigShort — home / product / FAQ / pricing: <https://bigshort.com/>, <https://bigshort.com/product>, <https://bigshort.com/faqs>, <https://bigshort.com/pricing>
- BigShort Help Center — Indicators & Key Concepts: <https://help.bigshort.com/en/articles/13337551-bigshort-indicators-key-concepts>
- BigShort Help Center — SmartFlow & MomoFlow: <https://help.bigshort.com/en/articles/10361455-understanding-smartflow-and-momoflow-indicators>
- BigShort Help Center — FastFlow & MomoFlow: <https://help.bigshort.com/docs/indicators/core-flow/fastflow-momoflow>
- BigShort Help Center — Charts & Navigation: <https://help.bigshort.com/en/articles/13337546-bigshort-charts-navigation>
- BigShort blog — launch/pricing notes: <https://blog.bigshort.com/welcome-to-bigshort/>
- Futurepedia — BigShort overview/pricing: <https://www.futurepedia.io/tool/bigshort>
- Repainting background (general): TradersPost blog, <https://blog.traderspost.io/article/what-is-repainting-in-tradingview-and-how-do-i-find-it-and-avoid-it>

*Note: BigShort's own domains are egress-blocked in the environment where this
was compiled; confirm all live specifics (pricing, exact history length, current
indicator definitions) against your logged-in account.*
