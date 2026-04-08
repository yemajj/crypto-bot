"""Tests for VolumeSignalStrategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cryptobot.core.types import Bar, Position
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.volume_signal import VolumeSignalStrategy

_SYMBOL = "BTC/USDT"
_TF = "1h"
_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_FLAT = Position(symbol=_SYMBOL, qty=Decimal("0"), avg_price=Decimal("0"))


def _bar(close: float, volume: float = 100.0, idx: int = 0) -> Bar:
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_T0 + timedelta(hours=idx),
        open=Decimal(str(close)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 1)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
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
    strat = VolumeSignalStrategy()
    bars = [_bar(100.0, idx=i) for i in range(6)]   # need window+2=7
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_below_average_volume_returns_zero():
    strat = VolumeSignalStrategy()
    # Prior 5 bars avg=100; current volume=50 (below avg)
    prior = [_bar(100.0, volume=100.0, idx=i) for i in range(6)]
    current = _bar(101.0, volume=50.0, idx=6)
    bars = prior + [current]
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_average_volume_exactly_returns_zero():
    strat = VolumeSignalStrategy()
    prior = [_bar(100.0, volume=100.0, idx=i) for i in range(6)]
    current = _bar(101.0, volume=100.0, idx=6)   # vol_ratio == 1.0
    bars = prior + [current]
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_high_volume_up_price_positive_score():
    strat = VolumeSignalStrategy()
    prior = [_bar(100.0, volume=100.0, idx=i) for i in range(6)]
    current = _bar(105.0, volume=300.0, idx=6)   # 3× avg → vol_factor=1.0, price up
    bars = prior + [current]
    score = strat.signal_score(_ctx(bars))
    assert score > 0.0
    assert score <= 1.0


def test_high_volume_down_price_negative_score():
    strat = VolumeSignalStrategy()
    prior = [_bar(105.0, volume=100.0, idx=i) for i in range(6)]
    current = _bar(100.0, volume=300.0, idx=6)   # 3× avg → vol_factor=1.0, price down
    bars = prior + [current]
    score = strat.signal_score(_ctx(bars))
    assert score < 0.0
    assert score >= -1.0


def test_three_times_avg_volume_factor_is_one():
    """vol_ratio=3 → vol_factor = min(1.0, (3-1)/2) = 1.0."""
    strat = VolumeSignalStrategy()
    prior = [_bar(100.0, volume=100.0, idx=i) for i in range(6)]
    current = _bar(101.0, volume=300.0, idx=6)
    bars = prior + [current]
    score = strat.signal_score(_ctx(bars))
    assert abs(score) == pytest.approx(1.0)


def test_two_times_avg_volume_factor_is_half():
    """vol_ratio=2 → vol_factor = min(1.0, (2-1)/2) = 0.5."""
    import pytest
    strat = VolumeSignalStrategy()
    prior = [_bar(100.0, volume=100.0, idx=i) for i in range(6)]
    current = _bar(101.0, volume=200.0, idx=6)   # 2× avg, price up
    bars = prior + [current]
    score = strat.signal_score(_ctx(bars))
    assert score == pytest.approx(0.5)


def test_zero_avg_volume_returns_zero():
    strat = VolumeSignalStrategy()
    prior = [_bar(100.0, volume=0.0, idx=i) for i in range(6)]
    current = _bar(101.0, volume=100.0, idx=6)
    bars = prior + [current]
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_registered_as_volume_signal():
    from cryptobot.strategy.registry import get_strategy
    from cryptobot.strategy.volume_signal import VolumeSignalStrategy as VS
    assert get_strategy("volume_signal") is VS


import pytest
