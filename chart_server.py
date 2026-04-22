"""
chart_server.py — Auto-refreshing EMA Cloud chart dashboard.

Starts a local web server and opens a live dashboard in your browser.
Charts refresh automatically every 5 minutes with the latest data.

Usage:
    python chart_server.py            # All watchlist symbols
    python chart_server.py TSLA NVDA  # Specific symbols
"""

import os
import sys
import json
import time
import sqlite3
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

try:
    import pandas as pd
except ImportError:
    os.system(f"{sys.executable} -m pip install pandas")
    import pandas as pd

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.io as pio
except ImportError:
    os.system(f"{sys.executable} -m pip install plotly")
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.io as pio

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
import config

PORT = 5678
REFRESH_SECONDS = 300  # 5 minutes
symbols = [s.upper() for s in sys.argv[1:]] if len(sys.argv) > 1 else config.DEFAULT_WATCHLIST[:4]

# Cache for chart HTML
chart_cache = {}
last_updated = {}


# ── EMA ───────────────────────────────────────────────────────
def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


# ── Fetch bars ────────────────────────────────────────────────
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
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df = df.resample("5min").agg({
            "open": "first", "high": "max",
            "low": "min", "close": "last", "volume": "sum"
        }).dropna()
        return df
    except Exception as e:
        print(f"[{symbol}] Fetch error: {e}")
        return pd.DataFrame()


# ── Load trades ───────────────────────────────────────────────
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


# ── Build chart HTML ──────────────────────────────────────────
def build_chart_html(symbol):
    df = fetch_bars(symbol)
    if df.empty:
        return f"<p style='color:#f85149'>No data available for {symbol}</p>"

    df["ema5"]  = calc_ema(df["close"], 5)
    df["ema8"]  = calc_ema(df["close"], 8)
    df["ema9"]  = calc_ema(df["close"], 9)
    df["ema12"] = calc_ema(df["close"], 12)
    df["ema34"] = calc_ema(df["close"], 34)
    df["ema50"] = calc_ema(df["close"], 50)

    trades = load_trades(symbol)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.78, 0.22], vertical_spacing=0.02,
    )

    # Candles
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"], name="Price",
        increasing_line_color="#3fb950", decreasing_line_color="#f85149",
    ), row=1, col=1)

    # Bias Cloud 34/50 (green)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema50"], fill=None, mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0), showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema34"], fill="tonexty",
        fillcolor="rgba(63,185,80,0.12)", mode="lines",
        line=dict(color="rgba(63,185,80,0.5)", width=1), name="Bias 34/50"), row=1, col=1)

    # Fast Cloud 5/12 (blue)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema12"], fill=None, mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0), showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema5"], fill="tonexty",
        fillcolor="rgba(88,166,255,0.18)", mode="lines",
        line=dict(color="rgba(88,166,255,0.8)", width=1.5), name="Fast 5/12"), row=1, col=1)

    # Pullback Zone 8/9 (orange)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema9"], fill=None, mode="lines",
        line=dict(color="rgba(0,0,0,0)", width=0), showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df["ema8"], fill="tonexty",
        fillcolor="rgba(255,163,61,0.15)", mode="lines",
        line=dict(color="rgba(255,163,61,0.7)", width=1), name="Pullback 8/9"), row=1, col=1)

    # Buy/Sell markers
    if not trades.empty:
        buys  = trades[trades["action"] == "BUY"]
        sells = trades[trades["action"] == "SELL"]
        if not buys.empty:
            bt, bp = [], []
            for _, row in buys.iterrows():
                idx = df.index.get_indexer([row["timestamp"]], method="nearest")[0]
                bt.append(df.index[idx]); bp.append(df["low"].iloc[idx] * 0.998)
            fig.add_trace(go.Scatter(x=bt, y=bp, mode="markers+text",
                marker=dict(symbol="triangle-up", size=14, color="#3fb950"),
                text=["BUY"]*len(bt), textposition="bottom center",
                textfont=dict(color="#3fb950", size=9), name="BUY"), row=1, col=1)
        if not sells.empty:
            st, sp = [], []
            for _, row in sells.iterrows():
                idx = df.index.get_indexer([row["timestamp"]], method="nearest")[0]
                st.append(df.index[idx]); sp.append(df["high"].iloc[idx] * 1.002)
            fig.add_trace(go.Scatter(x=st, y=sp, mode="markers+text",
                marker=dict(symbol="triangle-down", size=14, color="#f85149"),
                text=["SELL"]*len(st), textposition="top center",
                textfont=dict(color="#f85149", size=9), name="SELL"), row=1, col=1)

    # Volume
    colors = ["#3fb950" if c >= o else "#f85149" for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"],
        marker_color=colors, name="Volume", opacity=0.5), row=2, col=1)

    fig.update_layout(
        template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        font=dict(family="monospace", color="#c9d1d9"),
        xaxis_rangeslider_visible=False, height=520,
        margin=dict(l=50, r=20, t=30, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1,
                    font=dict(size=10)),
    )
    fig.update_xaxes(gridcolor="#21262d", zeroline=False)
    fig.update_yaxes(gridcolor="#21262d", zeroline=False)

    return pio.to_html(fig, include_plotlyjs=False, full_html=False, config={"displayModeBar": False})


# ── Refresh worker ────────────────────────────────────────────
def refresh_all():
    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Refreshing charts...")
        for sym in symbols:
            chart_cache[sym] = build_chart_html(sym)
            last_updated[sym] = datetime.now().strftime("%H:%M:%S")
            print(f"  {sym} updated")
        time.sleep(REFRESH_SECONDS)


# ── HTML Dashboard ────────────────────────────────────────────
def build_dashboard():
    tabs_html = ""
    charts_html = ""
    for i, sym in enumerate(symbols):
        active = "active" if i == 0 else ""
        tabs_html += f'<button class="tab {active}" onclick="showTab(\'{sym}\')" id="tab-{sym}">{sym}</button>'
        display = "block" if i == 0 else "none"
        chart = chart_cache.get(sym, "<p style='color:#888'>Loading...</p>")
        updated = last_updated.get(sym, "—")
        charts_html += f"""
        <div id="chart-{sym}" style="display:{display}">
            <div style="color:#444;font-size:11px;margin-bottom:4px">
                Last updated: <span id="upd-{sym}">{updated}</span> &nbsp;|&nbsp;
                Auto-refreshes every {REFRESH_SECONDS//60} min
            </div>
            <div id="plot-{sym}">{chart}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<title>Ripster Trader — Live Charts</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1117; color: #c9d1d9; font-family: monospace; padding: 16px; }}
  h1 {{ color: #58a6ff; font-size: 18px; margin-bottom: 12px; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
  .badge {{ background: #1f6feb; color: #fff; padding: 3px 10px; border-radius: 12px; font-size: 11px; }}
  .tabs {{ display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }}
  .tab {{ background: #161b22; border: 1px solid #30363d; color: #8b949e; padding: 6px 18px;
          border-radius: 6px; cursor: pointer; font-family: monospace; font-size: 13px; }}
  .tab.active {{ background: #1f6feb; border-color: #1f6feb; color: #fff; }}
  .tab:hover:not(.active) {{ background: #21262d; color: #c9d1d9; }}
  .countdown {{ color: #3fb950; font-size: 12px; }}
  .status {{ color: #3fb950; font-size: 12px; }}
</style>
</head>
<body>
<div class="header">
  <h1>Ripster EMA Cloud — Live Dashboard</h1>
  <span class="status">● PAPER TRADING &nbsp;|&nbsp;
    Next refresh in: <span class="countdown" id="countdown">{REFRESH_SECONDS}s</span>
  </span>
</div>
<div class="tabs">{tabs_html}</div>
<div id="charts">{charts_html}</div>

<script>
function showTab(sym) {{
  document.querySelectorAll('[id^="chart-"]').forEach(el => el.style.display = "none");
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('chart-' + sym).style.display = 'block';
  document.getElementById('tab-' + sym).classList.add('active');
}}

// Countdown timer
let secs = {REFRESH_SECONDS};
setInterval(() => {{
  secs--;
  if (secs <= 0) secs = {REFRESH_SECONDS};
  document.getElementById('countdown').textContent = secs + 's';
}}, 1000);

// Auto-reload page every {REFRESH_SECONDS} seconds to get fresh charts
setTimeout(() => location.reload(), {REFRESH_SECONDS * 1000});
</script>
</body>
</html>"""


# ── HTTP Handler ──────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress request logs

    def do_GET(self):
        html = build_dashboard().encode()
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.send_header("Content-length", len(html))
        self.end_headers()
        self.wfile.write(html)


# ── Main ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Ripster Chart Server starting...")
    print(f"Symbols: {symbols}")
    print(f"Auto-refresh: every {REFRESH_SECONDS//60} minutes")
    print()

    # Initial load
    print("Loading initial charts (may take 30s)...")
    for sym in symbols:
        print(f"  Fetching {sym}...")
        chart_cache[sym] = build_chart_html(sym)
        last_updated[sym] = datetime.now().strftime("%H:%M:%S")

    # Start background refresh thread
    t = threading.Thread(target=refresh_all, daemon=True)
    t.start()

    # Start server
    server = HTTPServer(("localhost", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\nDashboard ready at: {url}")
    print("Press Ctrl+C to stop.\n")
    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nChart server stopped.")
