"""Unit tests for PaperBroker.

No CCXT, no network, no DB. All tests use synthetic Bar and Order objects.

Key properties under test:
- Market orders fill immediately at mark price ± slippage.
- Slippage worsens the fill price for the taker (higher for buys, lower for sells).
- Limit orders are queued and fill only when the bar touches the limit price,
  at the limit price with maker fees (no extra slippage).
- Insufficient cash rejects market buy orders (returns REJECTED status, no fill).
- Submitting a market order without a mark price raises RuntimeError.
- Cancelling a limit order removes it from the queue.
- Position qty increases on buy, decreases on sell, goes to 0 on full close.
- equity() returns cash + mark-to-market position value.
- No short selling: sell qty is clamped to held qty.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.core.ids import new_order_id
from cryptobot.core.types import Bar, Order, OrderStatus, OrderType, Position, Side
from cryptobot.execution.fees import FeeModel
from cryptobot.execution.paper_broker import PaperBroker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SYMBOL = "BTC/USDT"
_TS0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bar(
    close: float,
    idx: int = 0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    c = Decimal(str(close))
    o = Decimal(str(open_)) if open_ is not None else c
    h = Decimal(str(high)) if high is not None else c * Decimal("1.01")
    l = Decimal(str(low)) if low is not None else c * Decimal("0.99")
    return Bar(
        symbol=_SYMBOL,
        timeframe="1h",
        ts_open=_TS0 + timedelta(hours=idx),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("10"),
    )


def _market_buy(qty: float = 0.1, ts_idx: int = 0) -> Order:
    return Order(
        order_id=new_order_id(),
        run_id="test",
        strategy_id="test",
        symbol=_SYMBOL,
        side=Side.BUY,
        qty=Decimal(str(qty)),
        order_type=OrderType.MARKET,
        limit_price=None,
        ts_submitted=_TS0 + timedelta(hours=ts_idx),
        status=OrderStatus.NEW,
    )


def _market_sell(qty: float = 0.1, ts_idx: int = 0) -> Order:
    return Order(
        order_id=new_order_id(),
        run_id="test",
        strategy_id="test",
        symbol=_SYMBOL,
        side=Side.SELL,
        qty=Decimal(str(qty)),
        order_type=OrderType.MARKET,
        limit_price=None,
        ts_submitted=_TS0 + timedelta(hours=ts_idx),
        status=OrderStatus.NEW,
    )


def _limit_buy(limit_price: float, qty: float = 0.1, ts_idx: int = 0) -> Order:
    return Order(
        order_id=new_order_id(),
        run_id="test",
        strategy_id="test",
        symbol=_SYMBOL,
        side=Side.BUY,
        qty=Decimal(str(qty)),
        order_type=OrderType.LIMIT,
        limit_price=Decimal(str(limit_price)),
        ts_submitted=_TS0 + timedelta(hours=ts_idx),
        status=OrderStatus.NEW,
    )


def _limit_sell(limit_price: float, qty: float = 0.1, ts_idx: int = 0) -> Order:
    return Order(
        order_id=new_order_id(),
        run_id="test",
        strategy_id="test",
        symbol=_SYMBOL,
        side=Side.SELL,
        qty=Decimal(str(qty)),
        order_type=OrderType.LIMIT,
        limit_price=Decimal(str(limit_price)),
        ts_submitted=_TS0 + timedelta(hours=ts_idx),
        status=OrderStatus.NEW,
    )


def _zero_fees() -> FeeModel:
    return FeeModel(taker_bps=0.0, maker_bps=0.0, slippage_bps=0.0)


def _real_fees() -> FeeModel:
    return FeeModel(taker_bps=10.0, maker_bps=5.0, slippage_bps=5.0)


def _broker(starting_cash: float = 10_000.0, fees: FeeModel | None = None) -> PaperBroker:
    return PaperBroker(starting_cash, fees or _zero_fees())


# ---------------------------------------------------------------------------
# Market order tests
# ---------------------------------------------------------------------------

def test_market_buy_raises_without_price():
    """submit() on a market order without update_price() must raise RuntimeError."""
    broker = _broker()
    with pytest.raises(RuntimeError, match="No mark price"):
        broker.submit(_market_buy())


def test_market_buy_fills_immediately():
    """Market BUY returns FILLED status and creates one fill."""
    broker = _broker()
    broker.update_price(_SYMBOL, Decimal("100"))
    submitted = broker.submit(_market_buy(qty=0.5))
    assert submitted.status == OrderStatus.FILLED
    fills = broker.recent_fills()
    assert len(fills) == 1
    assert float(fills[0].qty) == pytest.approx(0.5)


def test_market_buy_fill_price_no_slippage():
    """With zero slippage, fill price equals mark price."""
    broker = _broker(fees=_zero_fees())
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy())
    assert float(broker.recent_fills()[0].price) == pytest.approx(100.0)


def test_market_buy_fill_price_worsened_by_slippage():
    """With non-zero slippage, BUY fills above mark price."""
    broker = _broker(fees=_real_fees())
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy())
    fill_price = float(broker.recent_fills()[0].price)
    assert fill_price > 100.0


def test_market_sell_fill_price_worsened_by_slippage():
    """With non-zero slippage, SELL fills below mark price."""
    broker = _broker(starting_cash=10_000.0, fees=_real_fees())
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=1.0))
    broker.submit(_market_sell(qty=1.0))
    # Second fill is the sell.
    sell_fill = broker.recent_fills()[-1]
    assert float(sell_fill.price) < 100.0


def test_market_buy_fee_deducted_from_cash():
    """After a market buy with non-zero fees, cash is reduced by fee."""
    broker = _broker(starting_cash=10_000.0, fees=_real_fees())
    broker.update_price(_SYMBOL, Decimal("1000"))
    broker.submit(_market_buy(qty=1.0))
    # fill_price ≈ 1005 (0.05% slippage), fee ≈ 10.05 (0.10% taker)
    # cost ≈ 1015.05; equity should be roughly 10_000 (mark-to-market)
    assert broker.cash < 10_000.0


def test_market_buy_rejected_when_insufficient_cash():
    """Market BUY cost > cash → REJECTED, no fill, cash unchanged."""
    broker = _broker(starting_cash=50.0, fees=_zero_fees())
    broker.update_price(_SYMBOL, Decimal("1000"))
    submitted = broker.submit(_market_buy(qty=1.0))  # costs $1000
    assert submitted.status == OrderStatus.REJECTED
    assert len(broker.recent_fills()) == 0
    assert broker.cash == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Limit order tests
# ---------------------------------------------------------------------------

def test_limit_buy_queued_returns_accepted():
    """LIMIT BUY is queued and returns ACCEPTED (not filled immediately)."""
    broker = _broker()
    submitted = broker.submit(_limit_buy(limit_price=90.0))
    assert submitted.status == OrderStatus.ACCEPTED
    assert len(broker.recent_fills()) == 0


def test_limit_buy_fills_when_bar_low_touches():
    """LIMIT BUY fills when bar.low <= limit_price."""
    broker = _broker()
    broker.submit(_limit_buy(limit_price=95.0))
    bar = _bar(close=100.0, low=94.0)  # low=94 < 95 → touched
    fills = broker.settle_pending(bar)
    assert len(fills) == 1
    assert float(fills[0].price) == pytest.approx(95.0)  # fills at limit, not bar.low


def test_limit_buy_does_not_fill_when_not_touched():
    """LIMIT BUY does NOT fill when bar.low > limit_price."""
    broker = _broker()
    broker.submit(_limit_buy(limit_price=90.0))
    bar = _bar(close=100.0, low=95.0)  # low=95 > 90 → not touched
    fills = broker.settle_pending(bar)
    assert len(fills) == 0


def test_limit_sell_fills_when_bar_high_touches():
    """LIMIT SELL fills when bar.high >= limit_price."""
    broker = _broker()
    # Need a position first.
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=0.5))
    broker.submit(_limit_sell(limit_price=110.0, qty=0.5))
    bar = _bar(close=105.0, high=111.0)  # high=111 >= 110 → touched
    fills = broker.settle_pending(bar)
    assert len(fills) == 1
    assert float(fills[0].price) == pytest.approx(110.0)


def test_limit_sell_does_not_fill_when_not_touched():
    """LIMIT SELL does NOT fill when bar.high < limit_price."""
    broker = _broker()
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=0.5))
    broker.submit(_limit_sell(limit_price=120.0, qty=0.5))
    bar = _bar(close=105.0, high=110.0)  # high=110 < 120
    fills = broker.settle_pending(bar)
    assert len(fills) == 0


def test_limit_order_uses_maker_fee():
    """Limit fills use maker_bps (cheaper than taker_bps)."""
    fees = FeeModel(taker_bps=20.0, maker_bps=5.0, slippage_bps=0.0)
    broker = _broker(fees=fees)
    order = _limit_buy(limit_price=100.0, qty=1.0)
    broker.submit(order)
    bar = _bar(close=105.0, low=99.0)
    fills = broker.settle_pending(bar)
    assert len(fills) == 1
    # maker fee = 100 * 1 * 5/10000 = 0.05
    expected_fee = 100.0 * 1.0 * 5.0 / 10_000.0
    assert float(fills[0].fee) == pytest.approx(expected_fee, rel=1e-6)


def test_cancel_removes_limit_order():
    """cancel() prevents the order from filling on the next bar."""
    broker = _broker()
    order = _limit_buy(limit_price=95.0)
    submitted = broker.submit(order)
    broker.cancel(submitted.order_id)
    bar = _bar(close=100.0, low=90.0)
    fills = broker.settle_pending(bar)
    assert len(fills) == 0


def test_limit_orders_for_other_symbol_not_filled():
    """settle_pending() only fills orders for bar.symbol."""
    broker = _broker()
    # Submit a limit order for a different symbol.
    other_order = Order(
        order_id=new_order_id(),
        run_id="test",
        strategy_id="test",
        symbol="ETH/USDT",
        side=Side.BUY,
        qty=Decimal("1"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("1000"),
        ts_submitted=_TS0,
        status=OrderStatus.NEW,
    )
    broker.submit(other_order)
    bar = _bar(close=2000.0, low=900.0)  # BTC/USDT bar — should not match ETH/USDT order
    fills = broker.settle_pending(bar)
    assert len(fills) == 0


# ---------------------------------------------------------------------------
# Position tracking tests
# ---------------------------------------------------------------------------

def test_position_opens_on_market_buy():
    broker = _broker()
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=0.5))
    pos = broker.positions().get(_SYMBOL)
    assert pos is not None
    assert float(pos.qty) == pytest.approx(0.5)


def test_position_closes_fully_on_sell():
    broker = _broker()
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=0.5))
    broker.submit(_market_sell(qty=0.5))
    pos = broker.positions().get(_SYMBOL)
    assert pos is None or float(pos.qty) == pytest.approx(0.0)


def test_position_avg_price_is_weighted():
    """Two buys at different prices → avg_price is weighted average."""
    broker = _broker()
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=1.0))
    broker.update_price(_SYMBOL, Decimal("200"))
    broker.submit(_market_buy(qty=1.0))
    pos = broker.positions().get(_SYMBOL)
    assert pos is not None
    # avg_price = (100 * 1 + 200 * 1) / 2 = 150
    assert float(pos.avg_price) == pytest.approx(150.0)


def test_sell_clamped_to_held_qty():
    """Selling more than held is clamped — no negative position, no crash."""
    broker = _broker()
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=0.5))
    broker.submit(_market_sell(qty=2.0))  # wants to sell 2.0 but only holds 0.5
    pos = broker.positions().get(_SYMBOL)
    assert pos is None or float(pos.qty) == pytest.approx(0.0)
    # Cash should have increased (the 0.5 that was held was sold)
    assert broker.cash > 0.0


def test_sell_with_no_position_creates_zero_qty_fill():
    """Selling when flat results in a zero-qty fill, not an error."""
    broker = _broker()
    broker.update_price(_SYMBOL, Decimal("100"))
    submitted = broker.submit(_market_sell(qty=0.1))
    # Order completes without crashing; qty in fill is 0.
    fills = broker.recent_fills()
    assert len(fills) == 1
    assert float(fills[0].qty) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Equity tests
# ---------------------------------------------------------------------------

def test_equity_equals_cash_with_no_positions():
    broker = _broker(starting_cash=5_000.0)
    assert broker.equity() == pytest.approx(5_000.0)


def test_equity_includes_mark_to_market_gain():
    """Buy BTC at 100, price rises to 200 → equity > starting cash."""
    broker = _broker(starting_cash=10_000.0, fees=_zero_fees())
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=1.0))
    broker.update_price(_SYMBOL, Decimal("200"))
    equity = broker.equity()
    # cash ≈ 9900, pos_value = 1 * 200 = 200, total ≈ 10100
    assert equity > 10_000.0


def test_equity_includes_mark_to_market_loss():
    """Buy at 100, price drops to 50 → equity < starting cash."""
    broker = _broker(starting_cash=10_000.0, fees=_zero_fees())
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=1.0))
    broker.update_price(_SYMBOL, Decimal("50"))
    assert broker.equity() < 10_000.0


def test_cash_property_excludes_position_value():
    """broker.cash reflects only the cash component, not mark-to-market."""
    broker = _broker(starting_cash=10_000.0, fees=_zero_fees())
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=10.0))  # spends all cash
    broker.update_price(_SYMBOL, Decimal("1000"))
    # Even though equity is now large, cash should reflect spending
    assert broker.cash < 10_000.0
    assert broker.equity() > broker.cash


# ---------------------------------------------------------------------------
# Recent fills tests
# ---------------------------------------------------------------------------

def test_recent_fills_accumulates():
    broker = _broker()
    broker.update_price(_SYMBOL, Decimal("100"))
    broker.submit(_market_buy(qty=0.1))
    broker.submit(_market_buy(qty=0.1))
    assert len(broker.recent_fills()) == 2


def test_recent_fills_includes_limit_fills():
    broker = _broker()
    broker.submit(_limit_buy(limit_price=95.0))
    bar = _bar(close=100.0, low=94.0)
    broker.settle_pending(bar)
    assert len(broker.recent_fills()) == 1
