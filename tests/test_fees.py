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
    assert m.apply_slippage(price, buy=True) > price
    assert m.apply_slippage(price, buy=False) < price


def test_zero_slippage_is_identity():
    m = FeeModel(slippage_bps=0.0)
    price = Decimal("123.45")
    assert m.apply_slippage(price, buy=True) == Decimal("123.45000000")
    assert m.apply_slippage(price, buy=False) == Decimal("123.45000000")
