"""Tests for DonchianStrategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cryptobot.core.types import Bar, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.donchian import DonchianStrategy

_SYMBOL = "BTC/USDT"
_TF = "1h"
_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_FLAT = Position(symbol=_SYMBOL, qty=Decimal("0"), avg_price=Decimal("0"))


def _bar(close: float, high: float | None = None, low: float | None = None, idx: int = 0) -> Bar:
    h = high if high is not None else close + 1
    l = low if low is not None else close - 1
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_T0 + timedelta(hours=idx),
        open=Decimal(str(close)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(close)),
        volume=Decimal("1"),
    )


def _ctx(bars: list[Bar], params: dict | None = None) -> StrategyContext:
    return StrategyContext(
        symbol=_SYMBOL,
        history=bars,
        position=_FLAT,
        equity=10_000.0,
        params=params or {"window": 5},
    )


def test_too_few_bars_returns_zero():
    strat = DonchianStrategy()
    bars = [_bar(100.0, idx=i) for i in range(5)]   # need window+1=6
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_inside_channel_returns_zero():
    strat = DonchianStrategy()
    # Prior 5 bars: highs=101, lows=99. Current close=100 — inside channel.
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(5)]
    current = _bar(100.0, high=101.0, low=99.0, idx=5)
    bars = prior + [current]
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_breakout_above_channel_returns_one():
    strat = DonchianStrategy()
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(5)]
    current = _bar(102.0, high=103.0, low=101.0, idx=5)   # close > prior high of 101
    bars = prior + [current]
    assert strat.signal_score(_ctx(bars)) == 1.0


def test_breakdown_below_channel_returns_minus_one():
    strat = DonchianStrategy()
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(5)]
    current = _bar(97.0, high=98.0, low=96.0, idx=5)   # close < prior low of 99
    bars = prior + [current]
    assert strat.signal_score(_ctx(bars)) == -1.0


def test_exactly_at_channel_boundary_is_inside():
    strat = DonchianStrategy()
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(5)]
    # close == channel_high (101): not strictly greater, so inside
    current = _bar(101.0, high=102.0, low=100.0, idx=5)
    bars = prior + [current]
    # close > channel_high=101? No, close=101 == 101, not strictly >.
    # Actually close=101 and channel_high = max(high over prior 5) = 101
    # close > 101 is False → inside → 0.0
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_channel_uses_prior_bars_only():
    strat = DonchianStrategy()
    # Prior 5 bars have high=101. Current bar has high=200 but close=100.
    # Channel should be computed from prior bars only → high=101.
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(5)]
    current = _bar(100.0, high=200.0, low=50.0, idx=5)
    bars = prior + [current]
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_on_bar_generates_buy_on_breakout():
    strat = DonchianStrategy()
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(5)]
    current = _bar(105.0, high=106.0, low=104.0, idx=5)
    bars = prior + [current]
    params = {"window": 5, "atr_window": 3, "risk_per_trade_pct": 0.01, "buy_threshold": 0.5}
    ctx = StrategyContext(
        symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params=params
    )
    intents = strat.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].side == Side.BUY
    assert intents[0].stop_price is not None


def test_registered_as_donchian():
    from cryptobot.strategy.registry import get_strategy
    assert get_strategy("donchian") is DonchianStrategy
