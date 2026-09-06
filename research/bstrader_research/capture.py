"""
capture.py — forward data-capture harness for BigShort.

Why this exists
---------------
BigShort exposes no public API and no data export, and its on-screen HISTORICAL
values are recomputed with full-session information (look-ahead). Therefore the
only research-grade dataset is one captured FORWARD IN TIME: snapshot each
indicator's value as it is displayed live, at each 5-minute bar close, and never
touch it again. That is what this module builds.

What it does
------------
  * FeatureSnapshot: the row schema (timestamp + every registered feature).
  * CaptureWriter: append-only writer to CSV (human-inspectable) that can be
    compacted to parquet for the analysis harness (data.load_bigshort_features).
  * run_capture_loop: aligns to the next 5-minute boundary, calls a `reader`
    you supply to get the current values, stamps capture-time, and persists.

The `reader` is intentionally pluggable so this file has zero opinion about HOW
you read BigShort:
  - A Playwright reader that scrapes app.bigshort.com's rendered values is the
    intended production path (see research/capture_bigshort.py for a skeleton).
  - A manual reader (type the numbers each interval) works for a pilot.
Either way, capture-time == the moment you read it, so there is no look-ahead.

Integrity guarantees
--------------------
  * capture_ts (UTC, when we read) is stored alongside bar_ts (the 5-min bucket)
    so any clock skew or late read is auditable.
  * Rows are append-only; a re-read of the same bucket is stored as a new row
    with a higher `revision`, so you can detect whether BigShort REPAINTS (the
    displayed value for a past bucket changing later). Detecting repaint is a
    first-class goal, not a nuisance — see analyze_repaint().
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .signals import REGISTRY

FEATURE_KEYS = [f.key for f in REGISTRY]
SCHEMA = ["symbol", "bar_ts", "capture_ts", "revision", "price"] + FEATURE_KEYS


@dataclass
class FeatureSnapshot:
    symbol: str
    bar_ts: str                 # ISO8601 UTC of the 5-min bucket this value is for
    capture_ts: str             # ISO8601 UTC when we actually read it
    price: Optional[float] = None
    revision: int = 0
    values: dict = field(default_factory=dict)   # {feature_key: value}

    def as_row(self) -> dict:
        row = {"symbol": self.symbol, "bar_ts": self.bar_ts,
               "capture_ts": self.capture_ts, "revision": self.revision,
               "price": self.price}
        for k in FEATURE_KEYS:
            row[k] = self.values.get(k)
        return row


class CaptureWriter:
    """Append-only CSV writer with per-bucket revision tracking."""

    def __init__(self, symbol: str, out_dir: Optional[str] = None):
        self.symbol = symbol
        self.out_dir = out_dir or os.path.join(os.path.dirname(__file__), "..", "captured")
        os.makedirs(self.out_dir, exist_ok=True)
        self.csv_path = os.path.abspath(os.path.join(self.out_dir, f"bigshort_{symbol}.csv"))
        self._seen_revisions: dict[str, int] = {}
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=SCHEMA).writeheader()
        else:
            self._load_revisions()

    def _load_revisions(self):
        try:
            import pandas as pd
            df = pd.read_csv(self.csv_path)
            if not df.empty:
                self._seen_revisions = df.groupby("bar_ts")["revision"].max().to_dict()
        except Exception:
            pass

    def next_revision(self, bar_ts: str) -> int:
        return self._seen_revisions.get(bar_ts, -1) + 1

    def write(self, snap: FeatureSnapshot):
        snap.revision = self.next_revision(snap.bar_ts)
        self._seen_revisions[snap.bar_ts] = snap.revision
        with open(self.csv_path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=SCHEMA).writerow(snap.as_row())

    def compact_to_parquet(self) -> str:
        """
        Collapse to one row per bar (latest revision seen AT or BEFORE the bucket
        close — i.e. the point-in-time value, NOT a later repaint), indexed by
        bar_ts, and write the parquet the analysis harness reads.
        """
        import pandas as pd
        df = pd.read_csv(self.csv_path)
        if df.empty:
            raise RuntimeError("No captured rows yet.")
        df["bar_ts"] = pd.to_datetime(df["bar_ts"], utc=True)
        df["capture_ts"] = pd.to_datetime(df["capture_ts"], utc=True)
        # Point-in-time = the FIRST reading whose capture_ts is at/after bucket close.
        # We keep the earliest capture per bucket (revision 0) to avoid ingesting
        # any later repaint into the backtest feature set.
        pit = (df.sort_values("capture_ts")
                 .groupby("bar_ts", as_index=True)
                 .first())
        keep = ["price"] + FEATURE_KEYS
        pit = pit[keep]
        pq_path = self.csv_path.replace(".csv", ".parquet")
        pit.to_parquet(pq_path)
        return pq_path


def next_5min_boundary(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    minute = (now.minute // 5) * 5 + 5
    base = now.replace(second=0, microsecond=0, minute=0)
    return base + timedelta(minutes=minute)


def run_capture_loop(symbol: str,
                     reader: Callable[[str], dict],
                     writer: Optional[CaptureWriter] = None,
                     max_snapshots: Optional[int] = None,
                     sleep_fn: Callable[[float], None] | None = None,
                     now_fn: Callable[[], datetime] | None = None):
    """
    Capture loop. On each 5-minute boundary it calls reader(symbol), which must
    return a dict like {"price": 451.2, "nof": ..., "gex": ..., ...} using the
    feature keys in signals.REGISTRY. Missing keys are stored as null.

    reader / sleep_fn / now_fn are injectable so this is unit-testable without a
    browser or wall-clock waits.
    """
    import time
    sleep_fn = sleep_fn or time.sleep
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    writer = writer or CaptureWriter(symbol)

    count = 0
    while max_snapshots is None or count < max_snapshots:
        target = next_5min_boundary(now_fn())
        wait = (target - now_fn()).total_seconds()
        if wait > 0:
            sleep_fn(wait)
        capture_ts = now_fn()
        vals = reader(symbol) or {}
        snap = FeatureSnapshot(
            symbol=symbol,
            bar_ts=target.isoformat(),
            capture_ts=capture_ts.isoformat(),
            price=vals.pop("price", None),
            values={k: vals.get(k) for k in FEATURE_KEYS},
        )
        writer.write(snap)
        count += 1
    return writer


def analyze_repaint(symbol: str, out_dir: Optional[str] = None) -> "object":
    """
    Compare revision 0 (point-in-time) against later revisions for the same bucket
    to quantify whether BigShort repaints. Returns a DataFrame of buckets whose
    later-revision value differs from the first capture, per feature.
    """
    import pandas as pd
    out_dir = out_dir or os.path.join(os.path.dirname(__file__), "..", "captured")
    path = os.path.abspath(os.path.join(out_dir, f"bigshort_{symbol}.csv"))
    df = pd.read_csv(path)
    if df.empty:
        return df
    changes = []
    for bar_ts, grp in df.groupby("bar_ts"):
        if grp["revision"].max() == 0:
            continue
        grp = grp.sort_values("revision")
        first = grp.iloc[0]
        last = grp.iloc[-1]
        for k in FEATURE_KEYS:
            if pd.notna(first[k]) and pd.notna(last[k]) and first[k] != last[k]:
                changes.append({"bar_ts": bar_ts, "feature": k,
                                "first": first[k], "last": last[k],
                                "revisions": int(grp["revision"].max())})
    return pd.DataFrame(changes)
