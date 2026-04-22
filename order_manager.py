"""
order_manager.py — Alpaca paper/live order placement + circuit breaker.

Handles:
  • Position sizing (% of portfolio)
  • Limit order placement with configurable offset
  • Circuit breaker (max trades/day, max daily loss)
  • Position tracking (what we're holding and at what price)
  • End-of-day flat (closes all positions before market close)
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    ClosePositionRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus

import config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    qty: float
    avg_entry_price: float
    order_id: str
    entry_time: str


@dataclass
class TradeResult:
    success: bool
    symbol: str
    action: str           # "BUY" or "SELL"
    qty: float
    price: float
    order_id: str = ""
    reason: str = ""      # Why it failed (if success=False)


class CircuitBreaker:
    """
    Hard safety limits. Resets at midnight each trading day.
    """

    def __init__(self):
        self._date: date = date.today()
        self._trades_today: int = 0
        self._starting_portfolio_value: Optional[float] = None
        self._current_portfolio_value: Optional[float] = None

    def reset_if_new_day(self):
        today = date.today()
        if today != self._date:
            logger.info(f"New trading day ({today}) — circuit breaker reset")
            self._date = today
            self._trades_today = 0
            self._starting_portfolio_value = None

    def set_portfolio_value(self, value: float):
        if self._starting_portfolio_value is None:
            self._starting_portfolio_value = value
            logger.info(f"Starting portfolio value: ${value:,.2f}")
        self._current_portfolio_value = value

    def record_trade(self):
        self.reset_if_new_day()
        self._trades_today += 1
        logger.info(f"Circuit breaker: {self._trades_today}/{config.MAX_TRADES_PER_DAY} trades today")

    def can_trade(self) -> tuple[bool, str]:
        """Returns (allowed, reason). If not allowed, reason explains why."""
        self.reset_if_new_day()

        if self._trades_today >= config.MAX_TRADES_PER_DAY:
            msg = f"Max trades/day reached ({config.MAX_TRADES_PER_DAY})"
            logger.warning(f"CIRCUIT BREAKER: {msg}")
            return False, msg

        if (self._starting_portfolio_value and self._current_portfolio_value):
            loss_pct = (self._starting_portfolio_value - self._current_portfolio_value) / self._starting_portfolio_value
            if loss_pct >= config.MAX_DAILY_LOSS_PCT:
                msg = f"Max daily loss triggered ({loss_pct*100:.2f}% >= {config.MAX_DAILY_LOSS_PCT*100:.1f}%)"
                logger.warning(f"CIRCUIT BREAKER: {msg}")
                return False, msg

        return True, "ok"

    @property
    def trades_today(self) -> int:
        return self._trades_today

    @property
    def daily_pnl_pct(self) -> Optional[float]:
        if self._starting_portfolio_value and self._current_portfolio_value:
            return (self._current_portfolio_value - self._starting_portfolio_value) / self._starting_portfolio_value
        return None


class OrderManager:
    """
    Manages all order placement and position tracking.
    Talks to Alpaca via REST API.
    """

    def __init__(self):
        self._client = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.IS_PAPER_TRADING,
        )
        self.circuit_breaker = CircuitBreaker()
        self._positions: dict[str, Position] = {}
        self._refresh_portfolio_value()
        logger.info(
            f"OrderManager ready | "
            f"{'PAPER' if config.IS_PAPER_TRADING else '⚠️ LIVE'} mode | "
            f"Portfolio: ${self._portfolio_value:,.2f}"
        )

    # ── Portfolio ────────────────────────────────────────────────────

    def _refresh_portfolio_value(self) -> float:
        try:
            account = self._client.get_account()
            value = float(account.portfolio_value)
            self.circuit_breaker.set_portfolio_value(value)
            self._portfolio_value = value
            return value
        except Exception as e:
            logger.error(f"Could not fetch portfolio value: {e}")
            self._portfolio_value = 0.0
            return 0.0

    def _position_size(self, price: float) -> int:
        """Calculate shares to buy based on POSITION_SIZE_PCT of portfolio."""
        portfolio = self._refresh_portfolio_value()
        if portfolio <= 0 or price <= 0:
            return 0
        alloc = portfolio * config.POSITION_SIZE_PCT
        alloc = min(alloc, portfolio * config.MAX_POSITION_SIZE_PCT)
        qty = int(alloc / price)
        return max(qty, 0)

    # ── Order Placement ──────────────────────────────────────────────

    def buy(self, symbol: str, price: float) -> TradeResult:
        """
        Place a buy order. Checks circuit breaker first.
        Uses limit order by default (safer than market).
        """
        allowed, reason = self.circuit_breaker.can_trade()
        if not allowed:
            return TradeResult(False, symbol, "BUY", 0, price, reason=reason)

        if len(self._positions) >= config.MAX_OPEN_POSITIONS:
            return TradeResult(False, symbol, "BUY", 0, price,
                               reason=f"Max open positions ({config.MAX_OPEN_POSITIONS}) reached")

        if symbol in self._positions:
            return TradeResult(False, symbol, "BUY", 0, price,
                               reason=f"Already holding {symbol}")

        qty = self._position_size(price)
        if qty <= 0:
            return TradeResult(False, symbol, "BUY", 0, price,
                               reason="Position size calculated as 0 shares")

        try:
            if config.USE_LIMIT_ORDERS:
                limit_price = round(price + config.LIMIT_ORDER_OFFSET_CENTS, 2)
                order_data = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price,
                )
            else:
                order_data = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )

            order = self._client.submit_order(order_data)
            self._positions[symbol] = Position(
                symbol=symbol,
                qty=qty,
                avg_entry_price=price,
                order_id=str(order.id),
                entry_time=str(datetime.now()),
            )
            self.circuit_breaker.record_trade()
            logger.info(f"BUY order placed | {symbol} x{qty} @ ${price:.2f} | order_id={order.id}")
            return TradeResult(True, symbol, "BUY", qty, price, order_id=str(order.id))

        except Exception as e:
            logger.error(f"BUY order failed for {symbol}: {e}")
            return TradeResult(False, symbol, "BUY", qty, price, reason=str(e))

    def sell(self, symbol: str, price: float) -> TradeResult:
        """Close the position in `symbol`."""
        if symbol not in self._positions:
            return TradeResult(False, symbol, "SELL", 0, price,
                               reason=f"No open position in {symbol}")

        pos = self._positions[symbol]

        try:
            order = self._client.close_position(symbol)
            pnl = (price - pos.avg_entry_price) * pos.qty
            logger.info(
                f"SELL order placed | {symbol} x{pos.qty} @ ${price:.2f} | "
                f"P&L: ${pnl:+.2f} | order_id={order.id}"
            )
            del self._positions[symbol]
            return TradeResult(True, symbol, "SELL", pos.qty, price, order_id=str(order.id))

        except Exception as e:
            logger.error(f"SELL order failed for {symbol}: {e}")
            return TradeResult(False, symbol, "SELL", pos.qty, price, reason=str(e))

    def close_all_positions(self) -> list[TradeResult]:
        """
        Close everything — called at end of day or emergency stop.
        """
        results = []
        symbols = list(self._positions.keys())
        for symbol in symbols:
            try:
                self._client.close_position(symbol)
                pos = self._positions.pop(symbol, None)
                logger.info(f"EOD close: {symbol}")
                results.append(TradeResult(True, symbol, "SELL",
                                           pos.qty if pos else 0, 0.0, reason="EOD"))
            except Exception as e:
                logger.error(f"Failed to close {symbol}: {e}")
                results.append(TradeResult(False, symbol, "SELL", 0, 0.0, reason=str(e)))
        return results

    # ── Position Queries ─────────────────────────────────────────────

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    @property
    def open_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def portfolio_value(self) -> float:
        return self._portfolio_value

    def status_summary(self) -> str:
        cb = self.circuit_breaker
        pnl = cb.daily_pnl_pct
        pnl_str = f"{pnl*100:+.2f}%" if pnl is not None else "n/a"
        positions = ", ".join(self._positions.keys()) or "none"
        return (
            f"Portfolio: ${self._portfolio_value:,.2f} | "
            f"Daily P&L: {pnl_str} | "
            f"Trades today: {cb.trades_today}/{config.MAX_TRADES_PER_DAY} | "
            f"Open: {positions}"
        )
