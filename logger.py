"""
logger.py — SQLite trade database + performance metrics.

Every entry, exit, signal, and news check is logged here.
The report.py script reads this database to generate the HTML dashboard.
"""

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    symbol: str
    action: str           # "BUY" or "SELL"
    qty: float
    entry_price: float
    exit_price: float
    entry_time: str
    exit_time: str
    pnl: float
    pnl_pct: float
    signal_reason: str
    cloud_ema5: Optional[float]
    cloud_ema34: Optional[float]
    news_safe: bool
    news_reason: str
    confidence: float
    order_id: str
    session_date: str     # YYYY-MM-DD


class TradeLogger:
    """Persists all trading activity to a local SQLite database."""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or config.DB_PATH
        self._init_db()
        logger.info(f"TradeLogger initialised — DB: {self._db_path}")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol       TEXT    NOT NULL,
                    action       TEXT    NOT NULL,
                    qty          REAL    NOT NULL,
                    entry_price  REAL,
                    exit_price   REAL,
                    entry_time   TEXT,
                    exit_time    TEXT,
                    pnl          REAL    DEFAULT 0,
                    pnl_pct      REAL    DEFAULT 0,
                    signal_reason TEXT,
                    cloud_ema5   REAL,
                    cloud_ema34  REAL,
                    news_safe    INTEGER DEFAULT 1,
                    news_reason  TEXT,
                    confidence   REAL    DEFAULT 0,
                    order_id     TEXT,
                    session_date TEXT    NOT NULL,
                    created_at   TEXT    DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol       TEXT    NOT NULL,
                    action       TEXT    NOT NULL,
                    price        REAL    NOT NULL,
                    timestamp    TEXT    NOT NULL,
                    reason       TEXT,
                    confidence   REAL,
                    acted_on     INTEGER DEFAULT 0,
                    block_reason TEXT,
                    created_at   TEXT    DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_events (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT    NOT NULL,
                    message    TEXT,
                    created_at TEXT    DEFAULT (datetime('now'))
                )
            """)
            # Indexes for fast date queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(session_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")

    # ── Logging trades ────────────────────────────────────────────────

    def log_entry(
        self,
        symbol: str,
        qty: float,
        price: float,
        order_id: str,
        signal_reason: str = "",
        cloud_ema5: Optional[float] = None,
        cloud_ema34: Optional[float] = None,
        news_safe: bool = True,
        news_reason: str = "",
        confidence: float = 0.0,
    ) -> int:
        """Log a BUY trade entry. Returns the row ID."""
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades
                   (symbol, action, qty, entry_price, entry_time, signal_reason,
                    cloud_ema5, cloud_ema34, news_safe, news_reason, confidence,
                    order_id, session_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    symbol, "BUY", qty, price,
                    datetime.now().isoformat(),
                    signal_reason, cloud_ema5, cloud_ema34,
                    int(news_safe), news_reason, confidence,
                    order_id, str(date.today()),
                )
            )
            row_id = cur.lastrowid
            logger.debug(f"Logged BUY entry for {symbol} (row {row_id})")
            return row_id

    def log_exit(
        self,
        symbol: str,
        exit_price: float,
        order_id: str = "",
        signal_reason: str = "",
    ) -> bool:
        """Update the most recent open BUY trade with exit details."""
        with self._conn() as conn:
            # Find the most recent open entry for this symbol
            row = conn.execute(
                """SELECT id, qty, entry_price FROM trades
                   WHERE symbol=? AND action='BUY' AND exit_price IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (symbol,)
            ).fetchone()

            if not row:
                logger.warning(f"No open BUY trade found for {symbol} to log exit")
                return False

            qty         = row["qty"]
            entry_price = row["entry_price"]
            pnl         = (exit_price - entry_price) * qty
            pnl_pct     = (exit_price - entry_price) / entry_price if entry_price else 0

            conn.execute(
                """UPDATE trades
                   SET exit_price=?, exit_time=?, pnl=?, pnl_pct=?, order_id=COALESCE(NULLIF(order_id,''),?)
                   WHERE id=?""",
                (
                    exit_price,
                    datetime.now().isoformat(),
                    round(pnl, 2),
                    round(pnl_pct, 4),
                    order_id,
                    row["id"],
                )
            )
            emoji = "✅" if pnl >= 0 else "❌"
            logger.info(f"{emoji} EXIT logged: {symbol} entry=${entry_price:.2f} exit=${exit_price:.2f} P&L=${pnl:+.2f}")
            return True

    def log_signal(
        self,
        symbol: str,
        action: str,
        price: float,
        timestamp: str,
        reason: str,
        confidence: float,
        acted_on: bool,
        block_reason: str = "",
    ):
        """Log every signal (even blocked ones) for analysis."""
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO signals
                   (symbol, action, price, timestamp, reason, confidence, acted_on, block_reason)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (symbol, action, price, timestamp, reason, confidence, int(acted_on), block_reason)
            )

    def log_event(self, event_type: str, message: str):
        """Log a system event (start, stop, circuit breaker trigger, etc.)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO system_events (event_type, message) VALUES (?,?)",
                (event_type, message)
            )

    # ── Queries ───────────────────────────────────────────────────────

    def get_today_trades(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE session_date=? ORDER BY id",
                (str(date.today()),)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_trades(self, days: int = 30) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM trades
                   WHERE session_date >= date('now', ?)
                   ORDER BY session_date DESC, id DESC""",
                (f"-{days} days",)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_performance_summary(self, days: int = 30) -> dict:
        """Aggregate performance stats for the HTML dashboard."""
        trades = [t for t in self.get_trades(days) if t.get("exit_price")]
        if not trades:
            return {"total_trades": 0, "win_rate": 0, "total_pnl": 0,
                    "avg_win": 0, "avg_loss": 0, "profit_factor": 0}

        winners = [t for t in trades if t["pnl"] > 0]
        losers  = [t for t in trades if t["pnl"] <= 0]
        total_pnl   = sum(t["pnl"] for t in trades)
        gross_profit = sum(t["pnl"] for t in winners)
        gross_loss   = abs(sum(t["pnl"] for t in losers))

        return {
            "total_trades":   len(trades),
            "wins":           len(winners),
            "losses":         len(losers),
            "win_rate":       len(winners) / len(trades) if trades else 0,
            "total_pnl":      round(total_pnl, 2),
            "avg_win":        round(gross_profit / len(winners), 2) if winners else 0,
            "avg_loss":       round(gross_loss  / len(losers),  2) if losers  else 0,
            "profit_factor":  round(gross_profit / gross_loss, 2) if gross_loss else 0,
            "best_trade":     round(max((t["pnl"] for t in trades), default=0), 2),
            "worst_trade":    round(min((t["pnl"] for t in trades), default=0), 2),
        }
