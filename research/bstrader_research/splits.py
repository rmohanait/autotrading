"""
splits.py — chronological data splitting and walk-forward windows.

Intraday time-series must NEVER be shuffled: a random split leaks future
information into training. Every function here preserves time order.

  train_val_test : one chronological cut, default 60/20/20 by calendar time.
  walk_forward   : rolling (train, test) windows that march forward in time,
                   the gold-standard for OOS validation of an intraday system.

Both operate on the DataFrame's session dates so a day is never split across
two sets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import pandas as pd


@dataclass
class Split:
    name: str
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame


def _unique_sessions(bars: pd.DataFrame) -> list[str]:
    return sorted(bars["session"].unique().tolist())


def train_val_test(bars: pd.DataFrame,
                   train_frac: float = 0.6,
                   val_frac: float = 0.2) -> Split:
    """Single chronological 60/20/20 split by trading day."""
    days = _unique_sessions(bars)
    n = len(days)
    n_train = int(n * train_frac)
    n_val = int(n * val_frac)
    train_days = set(days[:n_train])
    val_days = set(days[n_train:n_train + n_val])
    test_days = set(days[n_train + n_val:])
    return Split(
        name=f"{days[0]}..{days[-1]}",
        train=bars[bars["session"].isin(train_days)],
        val=bars[bars["session"].isin(val_days)],
        test=bars[bars["session"].isin(test_days)],
    )


def walk_forward(bars: pd.DataFrame,
                 train_days: int = 60,
                 test_days: int = 20,
                 step_days: int | None = None) -> Iterator[tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Yield (train, test) DataFrames rolling forward. Default step == test_days so
    the test windows tile the timeline with no overlap.
    """
    step_days = step_days or test_days
    days = _unique_sessions(bars)
    i = 0
    while i + train_days + test_days <= len(days):
        tr = set(days[i:i + train_days])
        te = set(days[i + train_days:i + train_days + test_days])
        yield (bars[bars["session"].isin(tr)], bars[bars["session"].isin(te)])
        i += step_days
