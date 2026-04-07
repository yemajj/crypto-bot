"""Backtest broker: deterministic fills over historical bars.

Execution model:
- `submit(order)` queues the order and returns it as ACCEPTED.
- `settle_fills(bar)` runs at the START of each bar loop iteration.
  It fills all queued orders at bar.open ± slippage and deducts fees.
- `check_stops(bar)` runs after settle_fills.
  For each long position with a stop, if bar.low <= stop_price the position
  is closed at stop_price (with adverse slippage).
- `portfolio_value(prices)` returns cash + mark-to-market of open positions.

No async, no callbacks, no side effects beyond internal state.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from cryptobot.core.ids import new_order_id
from cryptobot.core.types import (
    Bar,
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
)
from cryptobot.execution.broker_base import Broker
from cryptobot.execution.fees import FeeModel


class BacktestBroker(Broker):
    def __init__(self, starting_cash: float, fees: FeeModel) -> None:
        self._cash: float = starting_cash
        self._fees = fees
        self._positions: dict[str, Position] = {}
        self._stops: dict[str, Decimal] = {}      # symbol → stop price
        self._fills: list[Fill] = []
        self._pending: list[Order] = []            # orders waiting to fill

    # ------------------------------------------------------------------
    # Broker ABC interface
    # ------------------------------------------------------------------

    def submit(self, order: Order) -> Order:
        """Queue order for fill at the next bar's open."""
        accepted = replace(order, status=OrderStatus.ACCEPTED)
        self._pending.append(accepted)
        return accepted

    def cancel(self, order_id: str) -> None:
        self._pending = [o for o in self._pending if o.order_id != order_id]

    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def equity(self) -> float:
        """Cash only. Use portfolio_value() for mark-to-market equity."""
        return self._cash

    def recent_fills(self) -> list[Fill]:
        return list(self._fills)

    # ------------------------------------------------------------------
    # Backtest-specific methods (called by BacktestEngine, not Broker ABC)
    # ------------------------------------------------------------------

    def settle_fills(self, bar: Bar) -> list[Fill]:
        """Fill all pending orders at bar.open with slippage and fees.

        Called at the very start of processing each new bar so that orders
        submitted on bar N fill at bar N+1's open — no look-ahead.
        """
        new_fills: list[Fill] = []
        for order in self._pending:
            fill = self._execute(order, bar.open, bar.ts_open)
            new_fills.append(fill)
        self._pending.clear()
        return new_fills

    def check_stops(self, bar: Bar) -> list[Fill]:
        """Close any long position whose stop was breached during bar.

        We fill at stop_price (not bar.low) with adverse slippage. This is
        slightly optimistic vs. gap-through but more realistic than ignoring
        the stop or filling at bar.low.
        """
        stop_fills: list[Fill] = []
        symbol = bar.symbol
        stop_price = self._stops.get(symbol)
        if stop_price is None:
            return stop_fills

        pos = self._positions.get(symbol)
        if pos is None or pos.qty <= Decimal("0"):
            self._stops.pop(symbol, None)
            return stop_fills

        if bar.low <= stop_price:
            # Synthetic market-SELL order to close the position.
            stop_order = Order(
                order_id=new_order_id(),
                run_id="stop",
                strategy_id="stop_loss",
                symbol=symbol,
                side=Side.SELL,
                qty=pos.qty,
                order_type=OrderType.MARKET,
                limit_price=None,
                ts_submitted=bar.ts_open,
                status=OrderStatus.ACCEPTED,
            )
            fill = self._execute(stop_order, stop_price, bar.ts_open)
            stop_fills.append(fill)
            self._stops.pop(symbol, None)

        return stop_fills

    def register_stop(self, symbol: str, stop_price: Decimal) -> None:
        """Store a stop price for an open long position."""
        self._stops[symbol] = stop_price

    def clear_stop(self, symbol: str) -> None:
        self._stops.pop(symbol, None)

    def portfolio_value(self, prices: dict[str, Decimal]) -> float:
        """Cash + mark-to-market value of all open positions."""
        pos_value = sum(
            float(pos.qty) * float(prices.get(sym, pos.avg_price))
            for sym, pos in self._positions.items()
            if pos.qty > Decimal("0")
        )
        return self._cash + pos_value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute(self, order: Order, raw_price: Decimal, ts: datetime) -> Fill:
        """Apply slippage, compute fee, update cash and positions, record fill."""
        is_buy = order.side == Side.BUY

        fill_price = self._fees.apply_slippage(raw_price, buy=is_buy)
        notional = fill_price * order.qty
        fee = self._fees.fee(notional, taker=True)

        if is_buy:
            cost = float(notional) + float(fee)
            self._cash -= cost
            self._update_position_buy(order.symbol, order.qty, fill_price)
        else:
            proceeds = float(notional) - float(fee)
            self._cash += proceeds
            self._update_position_sell(order.symbol, order.qty)

        fill = Fill(
            order_id=order.order_id,
            ts=ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts,
            price=fill_price,
            qty=order.qty,
            fee=fee,
            fee_currency="USDT",
        )
        self._fills.append(fill)
        return fill

    def _update_position_buy(
        self, symbol: str, qty: Decimal, price: Decimal
    ) -> None:
        existing = self._positions.get(symbol)
        if existing is None or existing.qty == Decimal("0"):
            self._positions[symbol] = Position(
                symbol=symbol, qty=qty, avg_price=price
            )
        else:
            total_qty = existing.qty + qty
            avg = (existing.qty * existing.avg_price + qty * price) / total_qty
            self._positions[symbol] = Position(
                symbol=symbol, qty=total_qty, avg_price=avg
            )

    def _update_position_sell(self, symbol: str, qty: Decimal) -> None:
        existing = self._positions.get(symbol)
        if existing is None:
            return
        new_qty = existing.qty - qty
        if new_qty <= Decimal("0"):
            self._positions[symbol] = Position(
                symbol=symbol, qty=Decimal("0"), avg_price=Decimal("0")
            )
        else:
            self._positions[symbol] = replace(existing, qty=new_qty)
