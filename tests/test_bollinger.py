"""Tests for BollingerStrategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cryptobot.core.types import Bar, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.bollinger import BollingerStrategy, _bands

_SYMBOL = "BTC/USDT"
_TF = "1h"
_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_FLAT = Position(symbol=_SYMBOL, qty=Decimal("0"), avg_price=Decimal("0"))


def _bar(close: float, idx: int = 0) -> Bar:
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_T0 + timedelta(hours=idx),
        open=Decimal(str(close)),
        high=Decimal(str(close + 0.5)),
        low=Decimal(str(close - 0.5)),
        close=Decimal(str(close)),
        volume=Decimal("1"),
    )


def _ctx(bars: list[Bar], params: dict | None = None) -> StrategyContext:
    return StrategyContext(
        symbol=_SYMBOL,
        history=bars,
        position=_FLAT,
        equity=10_000.0,
        params=params or {"window": 5, "n_std": 2.0},
    )


# ---------------------------------------------------------------------------
# _bands helper
# ---------------------------------------------------------------------------

def test_bands_flat_series_std_zero():
    middle, upper, lower, bw = _bands([100.0] * 5, n_std=2.0)
    assert middle == 100.0
    assert upper == 100.0
    assert lower == 100.0
    assert bw == 0.0


def test_bands_normal_series():
    closes = [98.0, 99.0, 100.0, 101.0, 102.0]
    middle, upper, lower, bw = _bands(closes, n_std=2.0)
    assert middle == 100.0
    assert upper > middle
    assert lower < middle
    assert bw > 0.0


# ---------------------------------------------------------------------------
# BollingerStrategy.signal_score
# ---------------------------------------------------------------------------

def test_too_few_bars_returns_zero():
    strat = BollingerStrategy()
    bars = [_bar(100.0, i) for i in range(5)]   # need window+1=6
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_flat_bars_std_zero_returns_zero():
    strat = BollingerStrategy()
    bars = [_bar(100.0, i) for i in range(7)]   # all identical → std=0
    assert strat.signal_score(_ctx(bars, params={"window": 5, "n_std": 2.0})) == 0.0


def test_contracting_bandwidth_returns_zero():
    strat = BollingerStrategy()
    # Wide prior window, narrowing current window
    prior_closes = [90.0, 95.0, 100.0, 105.0, 110.0]   # std ≈ 7.07
    curr_closes  = [98.0, 99.0, 100.0, 101.0, 102.0]   # std ≈ 1.41 (narrower)
    bars = [_bar(c, i) for i, c in enumerate(prior_closes + curr_closes[1:])]
    score = strat.signal_score(_ctx(bars, params={"window": 5, "n_std": 2.0}))
    assert score == 0.0


def test_expanding_bandwidth_close_above_midline_positive():
    strat = BollingerStrategy()
    # Narrow prior window, wide current window with close above middle
    prior_closes = [99.0, 100.0, 101.0, 100.0, 99.0]   # narrow
    curr_closes  = [80.0, 90.0, 100.0, 110.0, 120.0]   # wide, close=120 well above middle
    bars = [_bar(c, i) for i, c in enumerate(prior_closes + curr_closes[1:])]
    score = strat.signal_score(_ctx(bars, params={"window": 5, "n_std": 2.0}))
    assert score > 0.0


def test_expanding_bandwidth_close_below_midline_negative():
    strat = BollingerStrategy()
    prior_closes = [99.0, 100.0, 101.0, 100.0, 99.0]
    curr_closes  = [120.0, 110.0, 100.0, 90.0, 80.0]   # wide, close=80 below middle
    bars = [_bar(c, i) for i, c in enumerate(prior_closes + curr_closes[1:])]
    score = strat.signal_score(_ctx(bars, params={"window": 5, "n_std": 2.0}))
    assert score < 0.0


def test_score_clamped_to_minus_one_to_one():
    strat = BollingerStrategy()
    # Extreme expansion — score formula may exceed 1.0 before clamping
    prior_closes = [100.0] * 5
    curr_closes  = [100.0, 200.0, 300.0, 400.0, 500.0]
    bars = [_bar(c, i) for i, c in enumerate(prior_closes + curr_closes[1:])]
    score = strat.signal_score(_ctx(bars, params={"window": 5, "n_std": 2.0}))
    assert -1.0 <= score <= 1.0


def test_registered_as_bollinger():
    from cryptobot.strategy.registry import get_strategy
    assert get_strategy("bollinger") is BollingerStrategy
