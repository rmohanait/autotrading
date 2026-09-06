"""
signals.py — BigShort feature interface + forward-return predictive tests.

This module is the bridge between captured BigShort data and Phases 3-9. It does
two things:

1. Defines the canonical set of BigShort-derived features the study will test,
   each with the documented interpretation and the hypothesis to be checked.
   IMPORTANT: MomoFlow is NOT assumed bullish-when-rising. Per BigShort's docs it
   is emotional/discretionary ("dumb money") flow; both the direct and inverse
   readings are registered as competing hypotheses and the data decides.

2. Provides forward-return machinery (predictive_information) that measures
   whether a feature carries information about SPY/QQQ returns over 5/10/15/30/60
   minute horizons — the core of Phase 3 — with a proper information-coefficient
   and a sign/quantile breakdown, plus a shuffled-control p-value.

None of this fabricates BigShort values. Every function requires a real captured
feature frame; with no data you get an explicit error, never a made-up number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
#  Feature registry: what each BigShort field means and what we will test.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FeatureSpec:
    key: str                      # column name in the captured dataset
    label: str
    documented_meaning: str
    hypotheses: tuple[str, ...]   # competing directional hypotheses to test
    kind: str                     # "flow" | "regime" | "level" | "analogue"
    inverse_candidate: bool = False   # test the inverse reading too?


REGISTRY: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        "nof", "Net Options Flow",
        "Dollar-weighted buy-vs-sell options activity plotted in line with price.",
        ("nof>0 precedes positive forward return",
         "nof slope (rising) precedes positive forward return"),
        "flow",
    ),
    FeatureSpec(
        "nofa", "NOF Accumulation",
        "Intraday cumulative NOF inventory; longer-horizon implication than bar NOF.",
        ("nofa rising precedes positive forward return",
         "nofa divergence vs price precedes reversal"),
        "flow",
    ),
    FeatureSpec(
        "fastflow", "FastFlow",
        "Algorithmic/automated flow (HFT, rotational, MM hedging). Formerly named "
        "'SmartFlow' — beware definition drift across the history.",
        ("fastflow direction precedes same-direction return",
         "fastflow acceleration precedes continuation"),
        "flow",
    ),
    FeatureSpec(
        "smartflow", "SmartFlow (institutional)",
        "Real-time institutional order flow; claimed to lead price. Note SF3/SF3.1 "
        "are separate 'Elite' indicators — capture whichever you actually subscribe to.",
        ("smartflow direction precedes same-direction return",
         "smartflow vs price divergence precedes reversal"),
        "flow",
    ),
    FeatureSpec(
        "momoflow", "MomoFlow",
        "Emotional/discretionary ('dumb money') flow: retail + blocky discretionary.",
        ("DIRECT: momoflow rising precedes positive return",
         "INVERSE: momoflow rising precedes NEGATIVE return (fade the crowd)"),
        "flow", inverse_candidate=True,
    ),
    FeatureSpec(
        "gex", "Gamma Exposure",
        "Dealer gamma exposure. Sign hypothesised to separate vol regimes.",
        ("positive GEX -> lower realized move / mean-reverting",
         "negative GEX -> higher realized move / trending"),
        "regime",
    ),
    FeatureSpec(
        "gex_regime", "GEX Regime",
        "Discretised regime label derived from GEX (positive/negative/flip).",
        ("regime selects trend vs mean-reversion strategy",),
        "regime",
    ),
    FeatureSpec(
        "gravity_hp", "Gravity Hedge Point",
        "Level of most concentrated dealer exposure; price hypothesised to gravitate to it.",
        ("distance to gravity_hp predicts reversion toward it",),
        "level",
    ),
    FeatureSpec(
        "call_hp", "Call Hedge Point",
        "Upside dealer-hedging level.",
        ("approach from below -> rejection more likely than random level",),
        "level",
    ),
    FeatureSpec(
        "put_hp", "Put Hedge Point",
        "Downside dealer-hedging level.",
        ("approach from above -> rejection more likely than random level",),
        "level",
    ),
    FeatureSpec(
        "darkpool", "Dark Pool level/activity",
        "Institutional off-exchange prints / levels.",
        ("proximity to dark-pool level -> support/resistance vs random level",),
        "level",
    ),
    FeatureSpec(
        "honey_badger", "Honey Badger",
        "Proprietary composite signal.",
        ("signal state precedes same-direction return",),
        "flow",
    ),
    FeatureSpec(
        "similarity", "Similarity Search",
        "Historical analogue days; outcome distribution of matched days.",
        ("matched-day forward distribution predicts today's path",),
        "analogue",
    ),
)


REGISTRY_BY_KEY = {f.key: f for f in REGISTRY}


# ─────────────────────────────────────────────────────────────────────────────
#  Forward returns and predictive information (Phase 3 core)
# ─────────────────────────────────────────────────────────────────────────────
def forward_returns(bars: pd.DataFrame, horizons=(1, 2, 3, 6, 12)) -> pd.DataFrame:
    """
    Forward close-to-close returns at the given horizons (in bars).
    With 5-minute bars, (1,2,3,6,12) == (5,10,15,30,60) minutes.
    Returns are NaN where the horizon runs past end-of-session (no cross-day leak).
    """
    out = pd.DataFrame(index=bars.index)
    close = bars["close"]
    session = bars["session"]
    for h in horizons:
        fwd = close.shift(-h) / close - 1.0
        # Null out returns that cross a session boundary.
        same_session = session.shift(-h) == session
        out[f"fwd_{h}"] = fwd.where(same_session)
    return out


def information_coefficient(feature: pd.Series, fwd_ret: pd.Series) -> float:
    """Spearman rank correlation between a feature and a forward return (the IC)."""
    df = pd.concat([feature, fwd_ret], axis=1).dropna()
    if len(df) < 30:
        return float("nan")
    return df.iloc[:, 0].corr(df.iloc[:, 1], method="spearman")


def shuffled_pvalue(feature: pd.Series, fwd_ret: pd.Series,
                    n_iter: int = 500, seed: int = 0) -> float:
    """
    Block-preserving significance test for the IC. We compare the observed |IC|
    to a null built by CIRCULARLY SHIFTING the feature (preserves autocorrelation,
    breaks the feature<->return alignment) — never a plain shuffle, which would
    destroy the serial structure and understate the p-value.
    """
    df = pd.concat([feature, fwd_ret], axis=1).dropna()
    if len(df) < 60:
        return float("nan")
    f = df.iloc[:, 0].to_numpy()
    r = df.iloc[:, 1].to_numpy()
    obs = abs(_spearman(f, r))
    rng = np.random.default_rng(seed)
    n = len(f)
    hits = 0
    for _ in range(n_iter):
        shift = int(rng.integers(1, n - 1))
        fs = np.roll(f, shift)
        if abs(_spearman(fs, r)) >= obs:
            hits += 1
    return (hits + 1) / (n_iter + 1)


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ar = pd.Series(a).rank().to_numpy()
    br = pd.Series(b).rank().to_numpy()
    ar = ar - ar.mean()
    br = br - br.mean()
    denom = np.sqrt((ar * ar).sum() * (br * br).sum())
    return float((ar * br).sum() / denom) if denom else 0.0


def predictive_information(bars: pd.DataFrame,
                           features: pd.DataFrame,
                           feature_key: str,
                           horizons=(1, 2, 3, 6, 12),
                           transform: Optional[Callable[[pd.Series], pd.Series]] = None,
                           ) -> pd.DataFrame:
    """
    For one BigShort feature, report IC and shuffled p-value at each horizon.

    transform lets you test derived readings, e.g. slope (diff), inverse (negate),
    or divergence-vs-price, without mutating the captured data.

    Returns a tidy DataFrame: one row per horizon.
    """
    if feature_key not in features.columns:
        raise KeyError(
            f"'{feature_key}' not in captured features {list(features.columns)}. "
            "Capture it first — no synthetic fallback."
        )
    feat = features[feature_key]
    if transform is not None:
        feat = transform(feat)
    feat = feat.reindex(bars.index)

    fwd = forward_returns(bars, horizons)
    rows = []
    for h in horizons:
        col = f"fwd_{h}"
        ic = information_coefficient(feat, fwd[col])
        p = shuffled_pvalue(feat, fwd[col])
        rows.append({
            "feature": feature_key,
            "horizon_bars": h,
            "horizon_min": h * 5,
            "IC": ic,
            "p_value": p,
            "n": int(pd.concat([feat, fwd[col]], axis=1).dropna().shape[0]),
        })
    return pd.DataFrame(rows)
