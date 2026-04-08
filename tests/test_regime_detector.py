"""Tests for detect_regime()."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cryptobot.core.types import Bar
from cryptobot.strategy.regime_detector import Regime, _MIN_BARS, detect_regime

_SYMBOL = "BTC/USDT"
_TF = "1h"
_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bar(close: float, high: float | None = None, low: float | None = None, idx: int = 0) -> Bar:
    h = high if high is not None else close * 1.005
    l = low if low is not None else close * 0.995
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


def _flat_bars(n: int, price: float = 100.0) -> list[Bar]:
    return [_bar(price, idx=i) for i in range(n)]


def _trending_bars(n: int, start: float = 100.0, step: float = 0.5) -> list[Bar]:
    """Monotonically rising bars — both SMAs will slope upward."""
    return [_bar(start + i * step, idx=i) for i in range(n)]


def _spiked_bars(n: int, price: float = 100.0, spike_multiplier: float = 10.0) -> list[Bar]:
    """Flat bars except last bar has greatly inflated ATR (wide high-low range)."""
    bars = [_bar(price, idx=i) for i in range(n - 1)]
    # Last bar: huge true range to spike current ATR
    spike_range = price * spike_multiplier
    bars.append(Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_T0 + timedelta(hours=n - 1),
        open=Decimal(str(price)),
        high=Decimal(str(price + spike_range)),
        low=Decimal(str(price - spike_range)),
        close=Decimal(str(price)),
        volume=Decimal("1"),
    ))
    return bars


# ---------------------------------------------------------------------------

def test_insufficient_bars_returns_ranging():
    bars = _flat_bars(_MIN_BARS - 1)
    assert detect_regime(bars) == Regime.RANGING


def test_empty_bars_returns_ranging():
    assert detect_regime([]) == Regime.RANGING


def test_flat_bars_returns_ranging():
    """Flat series: SMAs slope=0, no ATR spike → RANGING."""
    bars = _flat_bars(_MIN_BARS)
    assert detect_regime(bars) == Regime.RANGING


def test_trending_up_returns_trending():
    """Monotonically rising bars → both SMA slopes positive → TRENDING."""
    bars = _trending_bars(_MIN_BARS, start=100.0, step=0.5)
    assert detect_regime(bars) == Regime.TRENDING


def test_trending_down_returns_trending():
    """Monotonically falling bars → both SMA slopes negative → TRENDING."""
    bars = _trending_bars(_MIN_BARS, start=200.0, step=-0.5)
    assert detect_regime(bars) == Regime.TRENDING


def test_atr_spike_returns_breakout_watch():
    """Last bar has enormous ATR → BREAKOUT_WATCH, even if trend is present."""
    bars = _spiked_bars(_MIN_BARS, price=100.0, spike_multiplier=50.0)
    assert detect_regime(bars) == Regime.BREAKOUT_WATCH


def test_breakout_watch_takes_priority_over_trending():
    """Even with a trend, ATR spike should return BREAKOUT_WATCH."""
    # Rising trend + spike on last bar
    trending = _trending_bars(_MIN_BARS - 1, start=100.0, step=0.5)
    spike_range = 100.0 * 50.0
    last_price = float(trending[-1].close) + 0.5
    spike_bar = Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_T0 + timedelta(hours=_MIN_BARS - 1),
        open=Decimal(str(last_price)),
        high=Decimal(str(last_price + spike_range)),
        low=Decimal(str(last_price - spike_range)),
        close=Decimal(str(last_price)),
        volume=Decimal("1"),
    )
    bars = trending + [spike_bar]
    assert detect_regime(bars) == Regime.BREAKOUT_WATCH
