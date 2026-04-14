from __future__ import annotations

from decimal import Decimal

from cryptobot.execution.fees import FeeModel


def test_taker_fee_basic():
    m = FeeModel(taker_bps=10.0, maker_bps=5.0, slippage_bps=0.0)
    # 10 bps on $10,000 notional = $10.00
    assert m.fee(Decimal("10000"), taker=True) == Decimal("10.00000000")


def test_maker_fee_basic():
    m = FeeModel(taker_bps=10.0, maker_bps=5.0, slippage_bps=0.0)
    assert m.fee(Decimal("10000"), taker=False) == Decimal("5.00000000")


def test_slippage_worsens_buy_price():
    m = FeeModel(slippage_bps=10.0)
    price = Decimal("100.00")
    qty = Decimal("1")  # notional = $100 → small order (1× slippage)
    assert m.apply_slippage(price, qty, buy=True) > price
    assert m.apply_slippage(price, qty, buy=False) < price


def test_zero_slippage_is_identity():
    m = FeeModel(slippage_bps=0.0)
    price = Decimal("123.45")
    qty = Decimal("1")
    assert m.apply_slippage(price, qty, buy=True) == Decimal("123.45000000")
    assert m.apply_slippage(price, qty, buy=False) == Decimal("123.45000000")


def test_slippage_tiers():
    """Larger orders attract proportionally higher slippage."""
    m = FeeModel(slippage_bps=10.0, small_order_threshold=1_000.0, large_order_threshold=10_000.0)
    price = Decimal("100.00")

    # Small: notional = $100 → 1× (10 bps)
    small_price = m.apply_slippage(price, Decimal("1"), buy=True)
    # Medium: notional = $5000 → 2× (20 bps)
    medium_price = m.apply_slippage(price, Decimal("50"), buy=True)
    # Large: notional = $20000 → 3× (30 bps)
    large_price = m.apply_slippage(price, Decimal("200"), buy=True)

    assert small_price < medium_price < large_price
