"""Core dataclasses used across the system.

These are deliberately small, immutable, and framework-free. No pandas, no
SQLAlchemy here — the goal is a clean domain vocabulary that every other
module can depend on without circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    # TODO: STOP, STOP_LIMIT once we need them (Phase 4+).


class OrderStatus(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV candle. Times are always UTC."""

    symbol: str
    timeframe: str  # e.g. "1h", "4h", "1d"
    ts_open: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Signal:
    """A strategy's view of the world at a point in time.

    Signals are *advisory*. They get converted into Intents by the strategy,
    then into Orders by the risk manager + broker.
    """

    strategy_id: str
    symbol: str
    ts: datetime
    strength: float  # in [-1.0, 1.0]; negative = short bias, positive = long
    reason: str      # human-readable, logged verbatim


@dataclass(frozen=True)
class Intent:
    """A strategy's desired action, before risk checks.

    Intents are expressed as target actions, not final orders. The risk
    manager may shrink, reject, or transform them.
    """

    strategy_id: str
    symbol: str
    side: Side
    qty: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None   # required stop-loss (Phase 5)
    reason: str = ""


@dataclass(frozen=True)
class Order:
    """An order as submitted to a broker."""

    order_id: str
    run_id: str
    strategy_id: str
    symbol: str
    side: Side
    qty: Decimal
    order_type: OrderType
    limit_price: Decimal | None
    ts_submitted: datetime
    status: OrderStatus = OrderStatus.NEW


@dataclass(frozen=True)
class Fill:
    """A (possibly partial) execution of an order."""

    order_id: str
    ts: datetime
    price: Decimal
    qty: Decimal
    fee: Decimal
    fee_currency: str


@dataclass(frozen=True)
class Position:
    """Net position in a single symbol."""

    symbol: str
    qty: Decimal          # signed: positive long, negative short
    avg_price: Decimal    # weighted average entry; zero when flat
