"""
generate_static_charts.py — Generates a static index.html dashboard.
Run by GitHub Actions every 15 minutes to update the live GitHub Pages site.
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

try:
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.io as pio
except ImportError:
    os.system(f"{sys.executable} -m pip install pandas plotly")
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.io as pio

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
import config

SYMBOLS = config.DEFAULT_WATCHLIST[:6]
OUT_PATH = os.path.join(os.path.dirname(__file__), "docs", "index.html")


def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def fetch_bars(symbol, days=3):
    try:
        client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=start, end=end, feed="iex",
        )
        bars = client.get_stock_bars(req)
        df = bars.df
        if df.empty:
            return pd.DataFrame()
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")
        df = df[["open","high","low","close","volume"]].copy()
        df = df.resample("5min").agg({
            "open":"first","high":"max","low":"min","close":"last","volume":"sum"
        }).dropna()
        return df
    except Exception as e:
        print(f"[{symbol}] Error: {e}")
        return pd.DataFrame()


def load_trades(symbol):
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


def build_chart(symbol):
    df = fetch_bars(symbol)
    if df.empty:
        return f"<p style='color:#f85149;padding:20px'>No data for {symbol}</p>"

    df["ema5"]  = calc_ema(df["close"], 5)
    df["ema8"]  = calc_ema(df["close"], 8)
    df["ema9"]  = calc_ema(df["close"], 9)
    df["ema12"] = calc_ema(df["close"], 12)
    df["ema34"] = calc_ema(df["close"], 34)
    df["ema50"] = calc_ema(df["close"], 50)

    # Current price info
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    change = last["close"] - prev["close"]
    change_pct = (change / prev["close"]) * 100
    color = "#3fb950" if change >= 0 else "#f85149"
    arrow = "▲" if change >= 0 else "▼"

    trades = load_trades(symbol)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.78, 0.22], vertical_spacing=0.02)

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="Price",
        increasing_line_color="#3fb950", decreasing_line_color="#f85149",
    ), row=1, col=1)

    # Bias Cloud 34/50
    fig.add_trace(go.Scatter(x=df.index, y=df["ema50"], fill=None, mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0), showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema34"], fill="tonexty",
        fillcolor="rgba(63,185,80,0.12)", mode="lines",
        line=dict(color="rgba(63,185,80,0.5)", width=1), name="Bias 34/50"), row=1, col=1)

    # Fast Cloud 5/12
    fig.add_trace(go.Scatter(x=df.index, y=df["ema12"], fill=None, mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0), showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema5"], fill="tonexty",
        fillcolor="rgba(88,166,255,0.18)", mode="lines",
        line=dict(color="rgba(88,166,255,0.8)", width=1.5), name="Fast 5/12"), row=1, col=1)

    # Pullback Zone 8/9
    fig.add_trace(go.Scatter(x=df.index, y=df["ema9"], fill=None, mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0), showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema8"], fill="tonexty",
        fillcolor="rgba(255,163,61,0.15)", mode="lines",
        line=dict(color="rgba(255,163,61,0.7)", width=1), name="Pullback 8/9"), row=1, col=1)

    # Trade markers
    if not trades.empty:
        for action, sym_marker, sym_color, pos in [
            ("BUY", "triangle-up", "#3fb950", "bottom center"),
            ("SELL", "triangle-down", "#f85149", "top center")
        ]:
            t_df = trades[trades["action"] == action]
            if not t_df.empty:
                xs, ys = [], []
                for _, row in t_df.iterrows():
                    idx = df.index.get_indexer([row["timestamp"]], method="nearest")[0]
                    xs.append(df.index[idx])
                    ys.append(df["low"].iloc[idx]*0.998 if action=="BUY" else df["high"].iloc[idx]*1.002)
                fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers+text",
                    marker=dict(symbol=sym_marker, size=14, color=sym_color),
                    text=[action]*len(xs), textposition=pos,
                    textfont=dict(color=sym_color, size=9), name=action), row=1, col=1)

    # Volume
    colors = ["#3fb950" if c >= o else "#f85149" for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"],
        marker_color=colors, name="Volume", opacity=0.5), row=2, col=1)

    fig.update_layout(
        template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        font=dict(family="monospace", color="#c9d1d9"),
        xaxis_rangeslider_visible=False, height=500,
        margin=dict(l=50, r=20, t=10, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.01,
                    xanchor="right", x=1, font=dict(size=10)),
    )
    fig.update_xaxes(gridcolor="#21262d", zeroline=False)
    fig.update_yaxes(gridcolor="#21262d", zeroline=False)

    chart_html = pio.to_html(fig, include_plotlyjs=False,
                              full_html=False, config={"displayModeBar": False})

    return f"""
    <div class="price-bar">
      <span class="sym">{symbol}</span>
      <span class="price">${last['close']:.2f}</span>
      <span style="color:{color}">{arrow} {abs(change):.2f} ({change_pct:+.2f}%)</span>
      <span class="vol">Vol: {int(last['volume']):,}</span>
    </div>
    {chart_html}"""


def generate():
    os.makedirs("docs", exist_ok=True)
    now = datetime.now().strftime("%b %d %Y, %I:%M %p")
    print(f"Generating charts at {now}...")

    tabs, panels = "", ""
    for i, sym in enumerate(SYMBOLS):
        print(f"  Building {sym}...")
        active = "active" if i == 0 else ""
        tabs += f'<button class="tab {active}" onclick="show(\'{sym}\')" id="t-{sym}">{sym}</button>\n'
        display = "block" if i == 0 else "none"
        chart = build_chart(sym)
        panels += f'<div id="p-{sym}" style="display:{display}">{chart}</div>\n'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="300">
<title>Ripster Trader — Live Charts</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0 }}
  body {{ background:#0d1117; color:#c9d1d9; font-family:monospace; padding:16px }}
  .header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; flex-wrap:wrap; gap:8px }}
  h1 {{ color:#58a6ff; font-size:17px }}
  .badge {{ background:#238636; color:#fff; padding:3px 10px; border-radius:12px; font-size:11px }}
  .updated {{ color:#444; font-size:11px }}
  .tabs {{ display:flex; gap:6px; margin-bottom:12px; flex-wrap:wrap }}
  .tab {{ background:#161b22; border:1px solid #30363d; color:#8b949e; padding:6px 16px;
          border-radius:6px; cursor:pointer; font-family:monospace; font-size:13px; transition:all .15s }}
  .tab.active {{ background:#1f6feb; border-color:#1f6feb; color:#fff }}
  .tab:hover:not(.active) {{ background:#21262d; color:#c9d1d9 }}
  .price-bar {{ display:flex; gap:16px; align-items:baseline; padding:8px 4px; margin-bottom:4px }}
  .sym {{ color:#58a6ff; font-size:20px; font-weight:bold }}
  .price {{ color:#fff; font-size:22px; font-weight:bold }}
  .vol {{ color:#444; font-size:12px }}
  .footer {{ color:#333; font-size:10px; margin-top:12px; text-align:center }}
</style>
</head>
<body>
<div class="header">
  <h1>⚡ Ripster EMA Cloud — Live Dashboard</h1>
  <div style="display:flex;gap:12px;align-items:center">
    <span class="badge">● PAPER TRADING</span>
    <span class="updated">Updated: {now} | Auto-refreshes every 5 min</span>
  </div>
</div>
<div class="tabs">{tabs}</div>
<div id="charts">{panels}</div>
<div class="footer">
  Automated paper trading system | Ripster EMA Cloud methodology | Not financial advice
</div>
<script>
function show(sym) {{
  document.querySelectorAll('[id^="p-"]').forEach(e => e.style.display="none");
  document.querySelectorAll('.tab').forEach(e => e.classList.remove('active'));
  document.getElementById('p-'+sym).style.display='block';
  document.getElementById('t-'+sym).classList.add('active');
}}
</script>
</body>
</html>"""

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Done! Saved to {OUT_PATH}")


if __name__ == "__main__":
    generate()
