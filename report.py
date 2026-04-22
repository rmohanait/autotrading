"""
report.py — Generate a self-contained HTML performance dashboard.

Run: python report.py
Opens a dashboard showing trade history, P&L, win rate, and signal log.
"""

import json
import os
import webbrowser
from datetime import datetime

from logger import TradeLogger


def generate_report(days: int = 30, open_browser: bool = True) -> str:
    """Generate HTML report and optionally open in browser. Returns file path."""
    db = TradeLogger()
    trades    = db.get_trades(days)
    perf      = db.get_performance_summary(days)
    today     = db.get_today_trades()

    html = _build_html(trades, perf, today, days)
    path = "report.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Report generated: {os.path.abspath(path)}")
    if open_browser:
        webbrowser.open(f"file://{os.path.abspath(path)}")
    return path


def _build_html(trades, perf, today, days):
    # Prepare chart data (daily P&L)
    daily = {}
    for t in trades:
        if t.get("exit_price") and t.get("session_date"):
            d = t["session_date"]
            daily[d] = daily.get(d, 0) + (t.get("pnl") or 0)
    chart_labels = json.dumps(sorted(daily.keys())[-30:])
    chart_data   = json.dumps([round(daily[d], 2) for d in sorted(daily.keys())[-30:]])

    # Recent trades table
    recent_rows = ""
    for t in trades[:50]:
        pnl     = t.get("pnl") or 0
        pnl_pct = t.get("pnl_pct") or 0
        has_exit = bool(t.get("exit_price"))
        pnl_clr = "#3fb950" if pnl >= 0 else "#f85149"
        pct_clr = "#3fb950" if pnl_pct >= 0 else "#f85149"
        exit_td = f"${t['exit_price']:.2f}" if has_exit else "OPEN"
        pnl_td  = f"${pnl:+.2f}" if has_exit else "—"
        pct_td  = f"{pnl_pct*100:+.2f}%" if has_exit else "—"
        recent_rows += (
            f"<tr>"
            f"<td>{t.get('session_date','')}</td>"
            f"<td>{t.get('symbol','')}</td>"
            f"<td>{t.get('qty',0):.0f}</td>"
            f"<td>${t.get('entry_price',0):.2f}</td>"
            f"<td>{exit_td}</td>"
            f"<td style='color:{pnl_clr}'>{pnl_td}</td>"
            f"<td style='color:{pct_clr}'>{pct_td}</td>"
            f"</tr>"
        )

    # Today's trades table
    today_rows = ""
    for t in today:
        pnl = t.get("pnl") or 0
        clr = "#3fb950" if pnl >= 0 else "#f85149"
        status = "OPEN" if not t.get("exit_price") else f"${pnl:+.2f}"
        today_rows += f"""
        <tr>
          <td>{t.get('symbol','')}</td>
          <td style="color:{'#58a6ff' if t['action']=='BUY' else '#f85149'}">{t['action']}</td>
          <td>${t.get('entry_price',0):.2f}</td>
          <td>{'$'+f"{t['exit_price']:.2f}" if t.get('exit_price') else '—'}</td>
          <td style="color:{clr}">{status}</td>
          <td>{t.get('confidence',0)*100:.0f}%</td>
          <td style="font-size:10px">{(t.get('signal_reason') or '')[:50]}</td>
        </tr>"""

    win_rate_pct = perf.get("win_rate", 0) * 100
    pnl_color    = "#3fb950" if perf.get("total_pnl", 0) >= 0 else "#f85149"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Ripster Trader Dashboard</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0d1117; color: #c9d1d9; font-family: 'Courier New', monospace; padding: 20px; }}
    h1 {{ color: #58a6ff; margin-bottom: 4px; }}
    .subtitle {{ color: #666; font-size: 12px; margin-bottom: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 20px; }}
    .card {{ background: #161b22; border: 1px solid #21262d; border-radius: 10px; padding: 14px; }}
    .card .label {{ font-size: 9px; color: #666; letter-spacing: 1px; margin-bottom: 4px; }}
    .card .value {{ font-size: 22px; font-weight: bold; }}
    .card .sub {{ font-size: 10px; color: #8b949e; margin-top: 2px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
    th {{ padding: 8px 6px; color: #666; font-size: 9px; letter-spacing: 1px; text-align: left;
          border-bottom: 1px solid #21262d; }}
    td {{ padding: 8px 6px; border-bottom: 1px solid #161b22; }}
    tr:hover {{ background: #161b22; }}
    .section {{ background: #161b22; border: 1px solid #21262d; border-radius: 10px;
                padding: 16px; margin-bottom: 16px; }}
    .section-title {{ font-size: 10px; color: #666; letter-spacing: 2px; margin-bottom: 12px; }}
    canvas {{ max-height: 200px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
              font-size: 9px; font-weight: bold; }}
    .paper {{ background: #58a6ff22; border: 1px solid #58a6ff55; color: #58a6ff; }}
  </style>
</head>
<body>
  <h1>📊 Ripster Trader Dashboard</h1>
  <p class="subtitle">
    {datetime.now().strftime('%A, %B %d, %Y %H:%M')} &nbsp;|&nbsp;
    Last {days} days &nbsp;|&nbsp;
    <span class="badge paper">PAPER TRADING</span>
  </p>

  <!-- KPI Cards -->
  <div class="grid">
    <div class="card">
      <div class="label">TOTAL P&L</div>
      <div class="value" style="color:{pnl_color}">${perf.get('total_pnl', 0):+.2f}</div>
      <div class="sub">{days}-day period</div>
    </div>
    <div class="card">
      <div class="label">WIN RATE</div>
      <div class="value" style="color:{'#3fb950' if win_rate_pct>=50 else '#f85149'}">{win_rate_pct:.1f}%</div>
      <div class="sub">{perf.get('wins',0)}W / {perf.get('losses',0)}L</div>
    </div>
    <div class="card">
      <div class="label">TOTAL TRADES</div>
      <div class="value" style="color:#58a6ff">{perf.get('total_trades', 0)}</div>
      <div class="sub">completed</div>
    </div>
    <div class="card">
      <div class="label">AVG WIN</div>
      <div class="value" style="color:#3fb950">${perf.get('avg_win', 0):.2f}</div>
      <div class="sub">per trade</div>
    </div>
    <div class="card">
      <div class="label">AVG LOSS</div>
      <div class="value" style="color:#f85149">-${perf.get('avg_loss', 0):.2f}</div>
      <div class="sub">per trade</div>
    </div>
    <div class="card">
      <div class="label">PROFIT FACTOR</div>
      <div class="value" style="color:{'#3fb950' if perf.get('profit_factor',0)>=1 else '#f85149'}">{perf.get('profit_factor', 0):.2f}</div>
      <div class="sub">&gt;1.5 is good</div>
    </div>
  </div>

  <!-- P&L Chart -->
  <div class="section">
    <div class="section-title">DAILY P&L (LAST 30 SESSIONS)</div>
    <canvas id="pnlChart"></canvas>
  </div>

  <!-- Today's Trades -->
  <div class="section">
    <div class="section-title">TODAY'S TRADES</div>
    <table>
      <thead>
        <tr><th>SYMBOL</th><th>ACTION</th><th>ENTRY</th><th>EXIT</th><th>P&L</th><th>CONF</th><th>REASON</th></tr>
      </thead>
      <tbody>
        {today_rows if today_rows else '<tr><td colspan="7" style="color:#444;padding:12px">No trades today</td></tr>'}
      </tbody>
    </table>
  </div>

  <!-- Recent Trades -->
  <div class="section">
    <div class="section-title">RECENT TRADES (LAST {days} DAYS)</div>
    <table>
      <thead>
        <tr><th>DATE</th><th>SYMBOL</th><th>QTY</th><th>ENTRY</th><th>EXIT</th><th>P&L</th><th>P&L%</th></tr>
      </thead>
      <tbody>
        {recent_rows or '<tr><td colspan="7" style="color:#444;padding:12px">No completed trades yet</td></tr>'}
      </tbody>
    </table>
  </div>

  <p style="color:#333;font-size:10px;margin-top:20px;text-align:center">
    Auto-generated by Ripster Trader — Paper Trading Mode — Not financial advice
  </p>

  <script>
    new Chart(document.getElementById('pnlChart'), {{
      type: 'bar',
      data: {{
        labels: {chart_labels},
        datasets: [{{
          label: 'Daily P&L ($)',
          data: {chart_data},
          backgroundColor: {chart_data}.map(v => v >= 0 ? 'rgba(63,185,80,0.6)' : 'rgba(248,81,73,0.6)'),
          borderColor:     {chart_data}.map(v => v >= 0 ? '#3fb950' : '#f85149'),
          borderWidth: 1,
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ ticks: {{ color: '#444', font: {{ size: 9 }} }}, grid: {{ color: '#161b22' }} }},
          y: {{ ticks: {{ color: '#444', callback: v => '$' + v }}, grid: {{ color: '#21262d' }} }}
        }}
      }}
    }});
  </script>
</body>
</html>"""


if __name__ == "__main__":
    generate_report(days=30, open_browser=True)
