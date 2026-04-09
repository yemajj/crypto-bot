"""Unit tests for SmaCrossover.on_bar().

All tests use synthetic bars; no network or file I/O required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

import cryptobot.strategy.sma_crossover  # noqa: F401 — registers strategy
from cryptobot.core.types import Bar, Intent, OrderType, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.registry import get_strategy
from cryptobot.strategy.sma_crossover import SmaCrossover, _atr, _sma

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)
_SYMBOL = "BTC/USDT"
_TF = "1h"

_PARAMS = {"fast": 3, "slow": 5, "atr_window": 3, "risk_per_trade_pct": 0.01}


def _bar(close: float, idx: int = 0, high: float | None = None, low: float | None = None) -> Bar:
    from datetime import timedelta
    c = Decimal(str(close))
    h = Decimal(str(high)) if high is not None else c * Decimal("1.001")
    l = Decimal(str(low)) if low is not None else c * Decimal("0.999")
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_TS + timedelta(hours=idx),
        open=c,
        high=h,
        low=l,
        close=c,
        volume=Decimal("10"),
    )


def _flat_position() -> Position:
    return Position(symbol=_SYMBOL, qty=Decimal("0"), avg_price=Decimal("0"))


def _long_position(qty: float = 0.01, avg: float = 50000.0) -> Position:
    return Position(symbol=_SYMBOL, qty=Decimal(str(qty)), avg_price=Decimal(str(avg)))


def _ctx(
    history: list[Bar],
    position: Position | None = None,
    equity: float = 10_000.0,
) -> StrategyContext:
    return StrategyContext(
        symbol=_SYMBOL,
        history=history,
        position=position or _flat_position(),
        equity=equity,
        params=_PARAMS,
    )


def _strategy() -> SmaCrossover:
    return SmaCrossover(params=_PARAMS)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

def test_sma_basic():
    assert _sma([1, 2, 3, 4, 5], 3) == pytest.approx(4.0)
    assert _sma([10, 20], 2) == pytest.approx(15.0)


def test_atr_returns_zero_when_not_enough_bars():
    bars = [_bar(100, i) for i in range(3)]  # need 4 for window=3
    assert _atr(bars, window=3) == 0.0


def test_atr_positive_for_bars_with_range():
    bars = [
        _bar(100, 0, high=102, low=98),
        _bar(101, 1, high=103, low=99),
        _bar(100, 2, high=102, low=98),
        _bar(102, 3, high=104, low=100),
    ]
    result = _atr(bars, window=3)
    assert result > 0


# ---------------------------------------------------------------------------
# Strategy tests
# ---------------------------------------------------------------------------

def test_no_signal_when_history_too_short():
    strat = _strategy()
    # Need at least max(slow, atr_window+1) + 1 = max(5, 4) + 1 = 6 bars
    bars = [_bar(100 + i, i) for i in range(5)]
    assert strat.on_bar(_ctx(bars)) == []


def test_no_signal_when_fast_already_above_slow_no_crossover():
    """Fast has been above slow for multiple bars — no new crossover."""
    strat = _strategy()
    # fast(3) > slow(5) for all of history: no crossover event
    closes = [100, 102, 104, 106, 108, 110, 112]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    result = strat.on_bar(_ctx(bars))
    assert result == []


def test_buy_signal_on_bullish_crossover():
    """fast crosses above slow between the second-to-last and last bar.

    With fast=3, slow=5 and closes=[100,100,100,100,50,50,50,200]:
      fast_prev = mean(50,50,50)=50  <= slow_prev = mean(100,100,50,50,50)=70
      fast_now  = mean(50,50,200)=100 > slow_now  = mean(100,50,50,50,200)=90
    """
    strat = _strategy()
    closes = [100, 100, 100, 100, 50, 50, 50, 200]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    intents = strat.on_bar(_ctx(bars, equity=10_000.0))
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == Side.BUY
    assert intent.order_type == OrderType.MARKET
    assert intent.qty > Decimal("0")
    assert intent.stop_price is not None
    assert intent.stop_price < Decimal(str(closes[-1]))


def test_no_buy_when_already_in_position():
    """Bullish crossover but already long — should not re-enter."""
    strat = _strategy()
    closes = [100, 100, 100, 100, 50, 50, 50, 200]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    intents = strat.on_bar(_ctx(bars, position=_long_position()))
    assert intents == []


def test_sell_signal_on_bearish_crossover():
    """fast crosses below slow while in a long position.

    With fast=3, slow=5 and closes=[50,50,50,200,200,200,200,100]:
      fast_prev = mean(200,200,200)=200 >= slow_prev = mean(50,200,200,200,200)=170
      fast_now  = mean(200,200,100)=167 <  slow_now  = mean(200,200,200,200,100)=180
    """
    strat = _strategy()
    closes = [50, 50, 50, 200, 200, 200, 200, 100]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    intents = strat.on_bar(_ctx(bars, position=_long_position(qty=0.01, avg=110.0)))
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == Side.SELL
    assert intent.qty == Decimal("0.01")
    assert intent.stop_price is None  # exit intents carry no stop


def test_no_sell_when_flat():
    """Bearish crossover but no position — nothing to close."""
    strat = _strategy()
    closes = [50, 50, 50, 200, 200, 200, 200, 100]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    intents = strat.on_bar(_ctx(bars, position=_flat_position()))
    assert intents == []


def test_stop_price_is_below_entry():
    """Stop price must always be strictly less than the signal bar close."""
    strat = _strategy()
    closes = [100, 100, 100, 100, 50, 50, 50, 200]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    intents = strat.on_bar(_ctx(bars))
    assert len(intents) == 1
    close = float(bars[-1].close)
    assert float(intents[0].stop_price) < close  # type: ignore[arg-type]


def test_qty_scales_with_equity():
    """Higher equity → larger position size (risk is fixed % of equity)."""
    strat = _strategy()
    closes = [100, 100, 100, 100, 50, 50, 50, 200]
    bars = [_bar(c, i) for i, c in enumerate(closes)]

    intents_small = strat.on_bar(_ctx(bars, equity=1_000.0))
    intents_large = strat.on_bar(_ctx(bars, equity=100_000.0))

    assert len(intents_small) == 1
    assert len(intents_large) == 1
    assert intents_large[0].qty > intents_small[0].qty


def test_strategy_registered():
    cls = get_strategy("sma_crossover")
    assert cls.name == "sma_crossover"


def test_notional_cap_clips_qty_when_atr_sizing_would_exceed_cap():
    """When ATR-derived qty × close > equity × max_position_notional_pct, qty is clamped.

    With closes=[100,100,100,100,50,50,50,200] and equity=10_000:
      ATR≈50.1 → stop_distance≈100.3 → uncapped qty≈0.997
      max_position_notional_pct=0.001 → max_qty = (10000*0.001)/200 = 0.05
      cap bites: result qty = 0.05, notional = 0.05 * 200 = 10.0 ≤ 10.0
    """
    strat = _strategy()
    closes = [100, 100, 100, 100, 50, 50, 50, 200]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    capped_params = {**_PARAMS, "max_position_notional_pct": 0.001}
    ctx = StrategyContext(
        symbol=_SYMBOL,
        history=bars,
        position=_flat_position(),
        equity=10_000.0,
        params=capped_params,
    )
    intents = strat.on_bar(ctx)
    assert len(intents) == 1
    close = float(bars[-1].close)
    # Notional must not exceed the cap
    assert float(intents[0].qty) * close <= 10_000.0 * 0.001


def test_notional_cap_is_noop_when_cap_is_1():
    """max_position_notional_pct=1.0 is a no-op — qty equals the uncapped ATR result."""
    strat = _strategy()
    closes = [100, 100, 100, 100, 50, 50, 50, 200]
    bars = [_bar(c, i) for i, c in enumerate(closes)]

    uncapped = strat.on_bar(_ctx(bars, equity=10_000.0))  # _PARAMS has no notional cap key
    capped_ctx = StrategyContext(
        symbol=_SYMBOL,
        history=bars,
        position=_flat_position(),
        equity=10_000.0,
        params={**_PARAMS, "max_position_notional_pct": 1.0},
    )
    capped = strat.on_bar(capped_ctx)

    assert len(uncapped) == 1 and len(capped) == 1
    assert capped[0].qty == uncapped[0].qty
