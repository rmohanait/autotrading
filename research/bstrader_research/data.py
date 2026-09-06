"""
data.py — point-in-time data loaders for the research harness.

Three OHLCV sources, all returning the same shape so the backtester is source-agnostic:

    a pandas DataFrame indexed by tz-aware UTC timestamp with columns
    ['open','high','low','close','volume'] plus a derived 'vwap' (session VWAP,
    reset each trading day) and an 'et' column (US/Eastern wall-clock time).

Sources
-------
load_alpaca_bars   : pulls N days of 5-minute bars from Alpaca (needs API keys).
load_csv_bars      : reads a CSV you exported yourself (columns: timestamp,open,
                     high,low,close,volume). Use this to feed any data vendor.
make_synthetic_bars: a deterministic random-walk generator used ONLY for the
                     end-to-end smoke test. It is clearly not real market data.

load_bigshort_features : reads a captured BigShort point-in-time dataset (see
                     capture.py). Raises FileNotFoundError until you have run the
                     forward-capture harness — there is no historical shortcut.

Why VWAP is computed here: the mandate repeatedly compares BigShort signals
against "information already available from price, volume and VWAP", so VWAP is a
first-class baseline feature, not a BigShort feature.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

OHLCV_COLS = ["open", "high", "low", "close", "volume"]
ET = "US/Eastern"


# ─────────────────────────────────────────────────────────────────────────────
#  Shared post-processing
# ─────────────────────────────────────────────────────────────────────────────
def _finalize(df: pd.DataFrame) -> pd.DataFrame:
    """Sort, add ET wall-clock, and add a session-anchored VWAP column."""
    df = df.sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df.copy()
    et = df.index.tz_convert(ET)
    df["et"] = et
    df["session"] = et.strftime("%Y-%m-%d")

    # Session VWAP: cumulative(typical*vol) / cumulative(vol), reset each day.
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    cum_pv = pv.groupby(df["session"], sort=False).cumsum()
    cum_vol = df["volume"].groupby(df["session"], sort=False).cumsum()
    df["vwap"] = (cum_pv / cum_vol.replace(0, np.nan)).fillna(df["close"])
    return df


def regular_session_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only 09:30–16:00 ET bars (drops pre/post-market)."""
    et = df["et"]
    mins = et.dt.hour * 60 + et.dt.minute
    mask = (mins >= 9 * 60 + 30) & (mins < 16 * 60)
    return df.loc[mask]


# ─────────────────────────────────────────────────────────────────────────────
#  Alpaca
# ─────────────────────────────────────────────────────────────────────────────
def load_alpaca_bars(symbol: str, days: int = 720, timeframe: str = "5Min",
                     api_key: Optional[str] = None,
                     secret_key: Optional[str] = None,
                     feed: str = "iex") -> pd.DataFrame:
    """
    Fetch `days` of bars for `symbol` from Alpaca.

    feed="iex" is available on the free tier (but is only IEX volume — thinner and
    not a true consolidated tape). feed="sip" is the full consolidated feed and
    needs a paid Alpaca data plan; use it for the real study when you can.

    Requires ALPACA_API_KEY / ALPACA_SECRET_KEY in the environment or passed in.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    api_key = api_key or os.getenv("ALPACA_API_KEY", "")
    secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        raise RuntimeError(
            "Alpaca keys missing. Set ALPACA_API_KEY / ALPACA_SECRET_KEY "
            "(a .env in the repo root works) before running a real backtest."
        )

    tf_map = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    }
    tf = tf_map.get(timeframe, TimeFrame(5, TimeFrameUnit.Minute))

    client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    req = StockBarsRequest(
        symbol_or_symbols=symbol, timeframe=tf, start=start, end=end, feed=feed,
    )
    raw = client.get_stock_bars(req).df
    if raw.empty:
        raise RuntimeError(f"No Alpaca data returned for {symbol}.")

    if isinstance(raw.index, pd.MultiIndex):
        raw = raw.xs(symbol, level="symbol")
    raw = raw[["open", "high", "low", "close", "volume"]]
    raw.index = pd.to_datetime(raw.index, utc=True)
    return _finalize(raw)


# ─────────────────────────────────────────────────────────────────────────────
#  CSV (vendor-agnostic)
# ─────────────────────────────────────────────────────────────────────────────
def load_csv_bars(path: str, tz_of_timestamp: str = "UTC") -> pd.DataFrame:
    """
    Load bars from a CSV with columns: timestamp, open, high, low, close, volume.
    `tz_of_timestamp` is the timezone the timestamps are written in ("UTC" or
    "US/Eastern"). Use this to plug in Polygon, Databento, IBKR exports, etc.
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts_col = next((c for c in ("timestamp", "time", "datetime", "date") if c in df.columns), None)
    if ts_col is None:
        raise ValueError("CSV needs a timestamp/time/datetime column.")
    idx = pd.to_datetime(df[ts_col])
    if idx.dt.tz is None:
        idx = idx.dt.tz_localize(tz_of_timestamp)
    df = df.set_index(idx)[OHLCV_COLS]
    return _finalize(df)


# ─────────────────────────────────────────────────────────────────────────────
#  Synthetic (smoke test only — NOT market data)
# ─────────────────────────────────────────────────────────────────────────────
def make_synthetic_bars(symbol: str = "SYN", days: int = 40, seed: int = 7,
                        start_price: float = 450.0) -> pd.DataFrame:
    """
    Deterministic intraday random walk with mild intraday trend/mean-reversion,
    used to prove the backtester runs end-to-end. This is NOT a market simulation
    and must never be used to make any claim about the strategy's real edge.
    """
    rng = np.random.default_rng(seed)
    bars_per_day = 78  # 6.5h / 5min
    rows = []
    price = start_price
    day0 = datetime(2025, 1, 6, tzinfo=timezone.utc)  # a Monday
    d = 0
    added = 0
    while added < days:
        day = day0 + timedelta(days=d)
        d += 1
        if day.weekday() >= 5:
            continue
        added += 1
        # open at 14:30 UTC (09:30 ET, ignoring DST for synthetic purposes)
        t = day.replace(hour=14, minute=30)
        drift = rng.normal(0, 0.0004)          # per-bar drift for the day
        for _ in range(bars_per_day):
            shock = rng.normal(drift, 0.0016)
            o = price
            c = o * (1 + shock)
            hi = max(o, c) * (1 + abs(rng.normal(0, 0.0007)))
            lo = min(o, c) * (1 - abs(rng.normal(0, 0.0007)))
            vol = float(rng.integers(50_000, 400_000))
            rows.append((t, o, hi, lo, c, vol))
            price = c
            t = t + timedelta(minutes=5)
    df = pd.DataFrame(rows, columns=["timestamp"] + OHLCV_COLS).set_index("timestamp")
    df.index = pd.to_datetime(df.index, utc=True)
    return _finalize(df)


# ─────────────────────────────────────────────────────────────────────────────
#  BigShort captured features
# ─────────────────────────────────────────────────────────────────────────────
def load_bigshort_features(symbol: str, path: Optional[str] = None) -> pd.DataFrame:
    """
    Load a captured point-in-time BigShort feature table for `symbol`, aligned to
    5-minute bar close timestamps (UTC index). Produced by capture.py.

    There is intentionally NO historical download path: BigShort exposes no API
    or export, and its on-screen historical values are recomputed with
    full-session information (look-ahead). The only sound dataset is one you
    capture forward in time. Until that file exists this raises FileNotFoundError
    — the harness never invents BigShort values.
    """
    path = path or os.path.join(
        os.path.dirname(__file__), "..", "captured", f"bigshort_{symbol}.parquet"
    )
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No captured BigShort data for {symbol} at {path}.\n"
            "Run the forward-capture harness (research/capture_bigshort.py) to "
            "build this dataset. Historical extraction is not available — see "
            "research/bigshort_data_access.md."
        )
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()
