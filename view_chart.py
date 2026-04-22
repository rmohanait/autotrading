"""
view_chart.py — Interactive EMA Cloud chart viewer.

Usage:
    python view_chart.py          # Charts all watchlist symbols
    python view_chart.py TSLA     # Chart a specific symbol
    python view_chart.py TSLA 5   # Symbol + timeframe in minutes (default 5)

Opens an interactive HTML chart in your browser showing:
  - Candlestick price bars
  - EMA 5/12 Fast Cloud (blue)
  - EMA 8/9  Pullback Zone (orange)
  - EMA 34/50 Bias Cloud (green/red)
  - Buy/Sell signals from trades.db
"""

import os
import sys
import sqlite3
import webbrowser
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Imports ───────────────────────────────────────────────────
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    print("Installing plotly...")
    os.system(f"{sys.executable} -m pip install plotly")
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

try:
    import pandas as pd
except ImportError:
    os.system(f"{sys.executable} -m pip install pandas")
    import pandas as pd

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
import config


# ── EMA calculation ───────────────────────────────────────────
def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ── Fetch bars from Alpaca ────────────────────────────────────
def fetch_bars(symbol: str, timeframe_min: int = 5, days: int = 3) -> pd.DataFrame:
    client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)

    tf_map = {1: TimeFrame.Minute, 5: TimeFrame.Minute, 15: TimeFrame.Minute, 60: TimeFrame.Hour}
    tf = tf_map.get(timeframe_min, TimeFrame.Minute)

    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=tf,
        start=start,
        end=end,
        feed="iex",
    )
    bars = client.get_stock_bars(req)
    df = bars.df

    if df.empty:
        print(f"No data for {symbol}")
        return pd.DataFrame()

    # Flatten multi-index if present
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol")

    df = df[["open", "high", "low", "close", "volume"]].copy()

    # For 5-min or 15-min, resample if needed
    if timeframe_min > 1:
        df = df.resample(f"{timeframe_min}min").agg({
            "open": "first", "high": "max",
            "low": "min", "close": "last", "volume": "sum"
        }).dropna()

    return df


# ── Load trades from DB ───────────────────────────────────────
def load_trades(symbol: str) -> pd.DataFrame:
    db_path = os.path.join(os.path.dirname(__file__), config.DB_PATH)
    if not os.path.exists(db_path):
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(
            "SELECT * FROM trades WHERE symbol=? ORDER BY timestamp",
            conn, params=(symbol,)
        )
        conn.close()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception:
        return pd.DataFrame()


# ── Build chart ───────────────────────────────────────────────
def build_chart(symbol: str, timeframe_min: int = 5) -> str:
    print(f"Fetching {symbol} bars...")
    df = fetch_bars(symbol, timeframe_min)
    if df.empty:
        return ""

    # Calculate all EMAs
    df["ema5"]  = calc_ema(df["close"], 5)
    df["ema8"]  = calc_ema(df["close"], 8)
    df["ema9"]  = calc_ema(df["close"], 9)
    df["ema12"] = calc_ema(df["close"], 12)
    df["ema34"] = calc_ema(df["close"], 34)
    df["ema50"] = calc_ema(df["close"], 50)

    trades = load_trades(symbol)

    # ── Build Plotly figure ───────────────────────────────────
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.03,
        subplot_titles=(f"{symbol} — Ripster EMA Cloud ({timeframe_min}min)", "Volume")
    )

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["open"], high=df["high"],
        low=df["low"],   close=df["close"],
        name="Price",
        increasing_line_color="#3fb950",
        decreasing_line_color="#f85149",
    ), row=1, col=1)

    # ── EMA Clouds (filled areas) ─────────────────────────────

    # Bias Cloud (EMA 34/50) — green above, red below
    fig.add_trace(go.Scatter(
        x=df.index, y=df["ema50"],
        fill=None, mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0),
        showlegend=False, name="EMA50"
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["ema34"],
        fill="tonexty",
        fillcolor="rgba(63,185,80,0.12)",
        mode="lines",
        line=dict(color="rgba(63,185,80,0.6)", width=1),
        name="Bias Cloud 34/50"
    ), row=1, col=1)

    # Fast Cloud (EMA 5/12) — blue
    fig.add_trace(go.Scatter(
        x=df.index, y=df["ema12"],
        fill=None, mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0),
        showlegend=False, name="EMA12"
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["ema5"],
        fill="tonexty",
        fillcolor="rgba(88,166,255,0.18)",
        mode="lines",
        line=dict(color="rgba(88,166,255,0.8)", width=1.5),
        name="Fast Cloud 5/12"
    ), row=1, col=1)

    # Pullback Zone (EMA 8/9) — orange
    fig.add_trace(go.Scatter(
        x=df.index, y=df["ema9"],
        fill=None, mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0),
        showlegend=False, name="EMA9"
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["ema8"],
        fill="tonexty",
        fillcolor="rgba(255,163,61,0.15)",
        mode="lines",
        line=dict(color="rgba(255,163,61,0.7)", width=1),
        name="Pullback Zone 8/9"
    ), row=1, col=1)

    # ── Buy / Sell markers from DB ────────────────────────────
    if not trades.empty:
        buys  = trades[trades["action"] == "BUY"]
        sells = trades[trades["action"] == "SELL"]

        if not buys.empty:
            buy_prices = []
            buy_times  = []
            for _, row in buys.iterrows():
                closest = df.index.get_indexer([row["timestamp"]], method="nearest")[0]
                buy_times.append(df.index[closest])
                buy_prices.append(df["low"].iloc[closest] * 0.998)
            fig.add_trace(go.Scatter(
                x=buy_times, y=buy_prices,
                mode="markers+text",
                marker=dict(symbol="triangle-up", size=14, color="#3fb950"),
                text=["BUY"] * len(buy_times),
                textposition="bottom center",
                textfont=dict(color="#3fb950", size=10),
                name="BUY Signal"
            ), row=1, col=1)

        if not sells.empty:
            sell_prices = []
            sell_times  = []
            for _, row in sells.iterrows():
                closest = df.index.get_indexer([row["timestamp"]], method="nearest")[0]
                sell_times.append(df.index[closest])
                sell_prices.append(df["high"].iloc[closest] * 1.002)
            fig.add_trace(go.Scatter(
                x=sell_times, y=sell_prices,
                mode="markers+text",
                marker=dict(symbol="triangle-down", size=14, color="#f85149"),
                text=["SELL"] * len(sell_times),
                textposition="top center",
                textfont=dict(color="#f85149", size=10),
                name="SELL Signal"
            ), row=1, col=1)

    # Volume bars
    colors = ["#3fb950" if c >= o else "#f85149"
              for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["volume"],
        marker_color=colors, name="Volume", opacity=0.6
    ), row=2, col=1)

    # ── Layout ────────────────────────────────────────────────
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font=dict(family="monospace", color="#c9d1d9"),
        xaxis_rangeslider_visible=False,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="right", x=1
        ),
        height=800,
        margin=dict(l=60, r=40, t=60, b=40),
        title=dict(
            text=f"<b>{symbol}</b> — Ripster EMA Cloud | Paper Trading",
            font=dict(size=16, color="#58a6ff")
        )
    )
    fig.update_xaxes(gridcolor="#21262d", zeroline=False)
    fig.update_yaxes(gridcolor="#21262d", zeroline=False)

    # Save and open
    out_path = os.path.join(os.path.dirname(__file__), f"chart_{symbol}.html")
    fig.write_html(out_path, include_plotlyjs="cdn")
    print(f"Chart saved: {out_path}")
    return out_path


# ── Main ──────────────────────────────────────────────────────
if __name__ == "__main__":
    symbols = [sys.argv[1].upper()] if len(sys.argv) > 1 else config.DEFAULT_WATCHLIST[:4]
    timeframe = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    print(f"Building charts for: {symbols} ({timeframe}min bars)")
    for sym in symbols:
        path = build_chart(sym, timeframe)
        if path:
            webbrowser.open(f"file:///{path.replace(chr(92), '/')}")

    print("Done! Charts opened in your browser.")
