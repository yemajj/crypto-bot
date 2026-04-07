"""Paper broker: simulates order execution against a live market feed.

Execution assumptions (deliberately conservative):
- Market orders fill immediately at the current bar's close price ± slippage.
  In reality an order would fill within seconds of bar close; this is a
  reasonable approximation for a bar-based system.
- Limit orders are filled when a subsequent bar's low (BUY) or high (SELL)
  touches the limit price. Fill price is the limit price — no further slippage
  for limit orders (they are maker-side fills).
- Fees: taker rate for market orders, maker rate for limit orders.
- No partial fills; orders are all-or-nothing.
- No short selling; SELL qty is clamped to current holdings.
- No leverage; positions are long-only in v1.

Call `update_price(symbol, price)` before `submit()` and before reading
`equity()`. The run loop is responsible for keeping prices current.
"""

from __future__ import annotations

import collections
from dataclasses import replace
from datetime import timezone
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
from cryptobot.monitoring.logging_setup import get_logger

log = get_logger(component="paper_broker")

_MAX_FILL_HISTORY = 1_000   # cap recent_fills() to avoid unbounded memory growth


class PaperBroker(Broker):
    def __init__(self, starting_cash: float, fees: FeeModel) -> None:
        self._cash: float = starting_cash
        self._fees = fees
        self._positions: dict[str, Position] = {}
        self._fills: collections.deque[Fill] = collections.deque(maxlen=_MAX_FILL_HISTORY)
        self._pending_limits: list[Order] = []   # limit orders awaiting fill
        self._mark_prices: dict[str, Decimal] = {}

    # ------------------------------------------------------------------
    # Price updates (called by the run loop before each bar is processed)
    # ------------------------------------------------------------------

    def update_price(self, symbol: str, price: Decimal) -> None:
        """Record the latest market price for mark-to-market equity calculation."""
        self._mark_prices[symbol] = price

    # ------------------------------------------------------------------
    # Broker ABC interface
    # ------------------------------------------------------------------

    def submit(self, order: Order) -> Order:
        """Submit a market or limit order.

        Market orders: fill immediately at current mark price ± slippage.
        Limit orders: queue for fill when a future bar touches the limit price.
        Returns the order with updated status (FILLED, ACCEPTED, or REJECTED).
        """
        if order.order_type == OrderType.MARKET:
            return self._submit_market(order)
        if order.order_type == OrderType.LIMIT:
            return self._submit_limit(order)
        raise ValueError(f"Unsupported order type: {order.order_type}")

    def cancel(self, order_id: str) -> None:
        """Cancel a pending limit order. No-op if order not found."""
        before = len(self._pending_limits)
        self._pending_limits = [o for o in self._pending_limits if o.order_id != order_id]
        if len(self._pending_limits) == before:
            log.warning("cancel_order_not_found", order_id=order_id)

    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def equity(self) -> float:
        """Cash + mark-to-market value of all open positions.

        Uses the most recently supplied price via update_price(). If no price
        has been set for a symbol, falls back to the position's avg_price
        (entry price), which will understate gains and overstate losses.
        """
        pos_value = sum(
            float(pos.qty) * float(self._mark_prices.get(sym, pos.avg_price))
            for sym, pos in self._positions.items()
            if pos.qty > Decimal("0")
        )
        return self._cash + pos_value

    def recent_fills(self) -> list[Fill]:
        return list(self._fills)

    # ------------------------------------------------------------------
    # Paper-specific methods (called by the run loop)
    # ------------------------------------------------------------------

    @property
    def cash(self) -> float:
        """Current cash balance (excludes unrealised position value)."""
        return self._cash

    def settle_pending(self, bar: Bar) -> list[Fill]:
        """Check pending limit orders against this bar's OHLC.

        BUY limit fills if bar.low <= limit_price.
        SELL limit fills if bar.high >= limit_price.
        Fill price is the limit price (maker fill — no extra slippage).
        Timestamp is bar.ts_open (conservative: we don't know the intra-bar time).

        Called at the START of each bar iteration, before new signals are generated.
        """
        new_fills: list[Fill] = []
        remaining: list[Order] = []

        for order in self._pending_limits:
            if order.symbol != bar.symbol:
                remaining.append(order)
                continue

            limit_price = order.limit_price
            if limit_price is None:
                log.error("limit_order_missing_price", order_id=order.order_id)
                remaining.append(order)
                continue

            touched = (
                (order.side == Side.BUY and bar.low <= limit_price)
                or (order.side == Side.SELL and bar.high >= limit_price)
            )

            if touched:
                fill = self._execute(order, limit_price, bar.ts_open, taker=False)
                new_fills.append(fill)
                log.info(
                    "limit_filled",
                    symbol=bar.symbol,
                    side=order.side.value,
                    qty=float(order.qty),
                    price=float(limit_price),
                )
            else:
                remaining.append(order)

        self._pending_limits = remaining
        return new_fills

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _submit_market(self, order: Order) -> Order:
        """Fill a market order at the current mark price ± slippage."""
        price = self._mark_prices.get(order.symbol)
        if price is None:
            raise RuntimeError(
                f"No mark price for {order.symbol!r}. "
                "Call update_price() before submitting a market order."
            )

        is_buy = order.side == Side.BUY
        fill_price = self._fees.apply_slippage(price, buy=is_buy)
        notional = fill_price * order.qty
        fee = self._fees.fee(notional, taker=True)

        if is_buy:
            cost = float(notional) + float(fee)
            if cost > self._cash:
                log.warning(
                    "market_order_rejected_insufficient_cash",
                    symbol=order.symbol,
                    needed=round(cost, 2),
                    available=round(self._cash, 2),
                )
                return replace(order, status=OrderStatus.REJECTED)

        self._execute(order, price, order.ts_submitted, taker=True)
        return replace(order, status=OrderStatus.FILLED)

    def _submit_limit(self, order: Order) -> Order:
        """Queue a limit order for future settlement."""
        if order.limit_price is None:
            raise ValueError(f"LIMIT order {order.order_id} missing limit_price")
        accepted = replace(order, status=OrderStatus.ACCEPTED)
        self._pending_limits.append(accepted)
        log.info(
            "limit_queued",
            symbol=order.symbol,
            side=order.side.value,
            qty=float(order.qty),
            limit_price=float(order.limit_price),
        )
        return accepted

    def _execute(
        self,
        order: Order,
        raw_price: Decimal,
        ts_naive,
        *,
        taker: bool,
    ) -> Fill:
        """Apply slippage (market only), compute fee, update cash/positions."""
        is_buy = order.side == Side.BUY

        # Slippage only for market (taker) orders; limit fills at the limit price.
        if taker:
            fill_price = self._fees.apply_slippage(raw_price, buy=is_buy)
        else:
            fill_price = raw_price

        notional = fill_price * order.qty
        fee = self._fees.fee(notional, taker=taker)

        # Ensure timezone-aware timestamp.
        ts = ts_naive.replace(tzinfo=timezone.utc) if ts_naive.tzinfo is None else ts_naive

        if is_buy:
            self._cash -= float(notional) + float(fee)
            self._update_position_buy(order.symbol, order.qty, fill_price)
        else:
            # Clamp sell qty to what we actually hold.
            held = self._positions.get(order.symbol)
            held_qty = held.qty if held else Decimal("0")
            actual_qty = min(order.qty, held_qty)
            if actual_qty <= Decimal("0"):
                log.warning(
                    "sell_order_nothing_to_sell",
                    symbol=order.symbol,
                    requested=float(order.qty),
                    held=float(held_qty),
                )
                # Still create a fill record with qty=0 so the caller can detect it.
                fill = Fill(
                    order_id=order.order_id,
                    ts=ts,
                    price=fill_price,
                    qty=Decimal("0"),
                    fee=Decimal("0"),
                    fee_currency="USDT",
                )
                self._fills.append(fill)
                return fill

            notional = fill_price * actual_qty
            fee = self._fees.fee(notional, taker=taker)
            self._cash += float(notional) - float(fee)
            self._update_position_sell(order.symbol, actual_qty)

        fill = Fill(
            order_id=order.order_id,
            ts=ts,
            price=fill_price,
            qty=order.qty if is_buy else actual_qty,  # type: ignore[possibly-undefined]
            fee=fee,
            fee_currency="USDT",
        )
        self._fills.append(fill)
        return fill

    def _update_position_buy(self, symbol: str, qty: Decimal, price: Decimal) -> None:
        existing = self._positions.get(symbol)
        if existing is None or existing.qty == Decimal("0"):
            self._positions[symbol] = Position(symbol=symbol, qty=qty, avg_price=price)
        else:
            total_qty = existing.qty + qty
            avg = (existing.qty * existing.avg_price + qty * price) / total_qty
            self._positions[symbol] = Position(symbol=symbol, qty=total_qty, avg_price=avg)

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
