"""Tests for EnsembleStrategy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.core.types import Bar, Intent, OrderType, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.ensemble import EnsembleStrategy
from cryptobot.strategy.regime_detector import _MIN_BARS

_SYMBOL = "BTC/USDT"
_TF = "1h"
_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_FLAT = Position(symbol=_SYMBOL, qty=Decimal("0"), avg_price=Decimal("0"))
_LONG = Position(symbol=_SYMBOL, qty=Decimal("0.1"), avg_price=Decimal("100"))


def _bar(close: float, high: float | None = None, low: float | None = None,
         volume: float = 100.0, idx: int = 0) -> Bar:
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
        volume=Decimal(str(volume)),
    )


def _trending_bars(n: int) -> list[Bar]:
    """Monotonically rising bars for testing regime + strategy signals."""
    return [_bar(100.0 + i * 0.5, idx=i) for i in range(n)]


def _flat_bars(n: int, price: float = 100.0) -> list[Bar]:
    return [_bar(price, idx=i) for i in range(n)]


def _ensemble_params(**overrides) -> dict:
    base = {
        "strategies": [
            {"name": "rsi", "bucket": "momentum",
             "params": {"window": 14, "oversold": 30.0, "overbought": 70.0}},
            {"name": "donchian", "bucket": "breakout",
             "params": {"window": 20}},
        ],
        "buy_threshold": 0.30,
        "sell_threshold": -0.30,
        "min_agreeing_buckets": 1,   # relaxed for unit tests
        "agreement_min_magnitude": 0.25,
        "atr_window": 14,
        "stop_distance_multiplier": 1.5,
        "risk_per_trade_pct": 0.01,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Registration and construction
# ---------------------------------------------------------------------------

def test_registered_as_ensemble():
    from cryptobot.strategy.registry import get_strategy
    assert get_strategy("ensemble") is EnsembleStrategy


def test_empty_strategies_raises():
    with pytest.raises(ValueError, match="at least one"):
        EnsembleStrategy({"strategies": []})


def test_unknown_sub_strategy_raises():
    with pytest.raises(KeyError):
        EnsembleStrategy({"strategies": [{"name": "does_not_exist", "bucket": "trend", "params": {}}]})


# ---------------------------------------------------------------------------
# BUY signal
# ---------------------------------------------------------------------------

def test_buy_intent_when_score_exceeds_threshold():
    """Strongly oversold RSI + Donchian breakout → BUY."""
    # Build a bar series where RSI is oversold (declining prices)
    # and Donchian shows a breakdown then recovery breakout.
    n = max(_MIN_BARS, 60)
    # Declining prices to make RSI oversold
    bars = [_bar(200.0 - i * 0.5, idx=i) for i in range(n)]

    ensemble = EnsembleStrategy(_ensemble_params(
        strategies=[
            {"name": "rsi", "bucket": "momentum",
             "params": {"window": 14, "oversold": 99.0}},  # threshold so high RSI is always "oversold"
        ],
        min_agreeing_buckets=1,
        buy_threshold=0.01,   # very low threshold to guarantee trigger
    ))
    ctx = StrategyContext(
        symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params={}
    )
    intents = ensemble.on_bar(ctx)
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == Side.BUY
    assert intent.stop_price is not None
    assert intent.order_type == OrderType.MARKET
    assert intent.strategy_id == "ensemble"


def test_buy_intent_has_stop_price():
    """RequireStopLoss compliance: BUY intents always include stop_price."""
    n = max(_MIN_BARS, 60)
    bars = [_bar(200.0 - i * 0.5, idx=i) for i in range(n)]
    ensemble = EnsembleStrategy(_ensemble_params(
        strategies=[{"name": "rsi", "bucket": "momentum",
                     "params": {"window": 14, "oversold": 99.0}}],
        min_agreeing_buckets=1,
        buy_threshold=0.01,
    ))
    ctx = StrategyContext(symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params={})
    intents = ensemble.on_bar(ctx)
    if intents:
        assert all(i.stop_price is not None for i in intents if i.side == Side.BUY)


# ---------------------------------------------------------------------------
# SELL signal
# ---------------------------------------------------------------------------

def test_sell_intent_when_in_long_position_and_score_negative():
    n = max(_MIN_BARS, 60)
    # Rising prices → RSI overbought → negative score
    bars = [_bar(100.0 + i * 0.5, idx=i) for i in range(n)]
    ensemble = EnsembleStrategy(_ensemble_params(
        strategies=[{"name": "rsi", "bucket": "momentum",
                     "params": {"window": 14, "overbought": 1.0}}],   # always overbought
        min_agreeing_buckets=1,
        sell_threshold=-0.01,
    ))
    ctx = StrategyContext(symbol=_SYMBOL, history=bars, position=_LONG, equity=10_000.0, params={})
    intents = ensemble.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].side == Side.SELL
    assert intents[0].qty == _LONG.qty


def test_no_sell_when_flat():
    n = max(_MIN_BARS, 60)
    bars = [_bar(100.0 + i * 0.5, idx=i) for i in range(n)]
    ensemble = EnsembleStrategy(_ensemble_params(
        strategies=[{"name": "rsi", "bucket": "momentum",
                     "params": {"window": 14, "overbought": 1.0}}],
        min_agreeing_buckets=1,
        sell_threshold=-0.01,
    ))
    ctx = StrategyContext(symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params={})
    intents = ensemble.on_bar(ctx)
    assert intents == []


# ---------------------------------------------------------------------------
# Agreement filter
# ---------------------------------------------------------------------------

def test_no_agreement_returns_empty():
    n = max(_MIN_BARS, 60)
    bars = _flat_bars(n)
    ensemble = EnsembleStrategy(_ensemble_params(
        strategies=[
            {"name": "rsi", "bucket": "momentum", "params": {"window": 14}},
            {"name": "donchian", "bucket": "breakout", "params": {"window": 20}},
        ],
        min_agreeing_buckets=3,   # impossible to satisfy with 2 buckets
        buy_threshold=0.01,
    ))
    ctx = StrategyContext(symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params={})
    assert ensemble.on_bar(ctx) == []


# ---------------------------------------------------------------------------
# Sub-strategy params isolation
# ---------------------------------------------------------------------------

def test_sub_strategy_receives_its_own_params_not_ensemble_params():
    """RSI sub-strategy must use window=14, not the ensemble atr_window=99.

    We use enough bars for the ensemble's atr_window=99 (needs 100 bars) so
    that a successful BUY intent can be generated.  If RSI mistakenly received
    atr_window=99 as its own 'window' it would return 0.0 (insufficient data)
    and produce no intent.
    """
    n = 120   # > 99+1 so ensemble ATR sizing works
    bars = [_bar(200.0 - i * 0.5, idx=i) for i in range(n)]
    ensemble = EnsembleStrategy({
        "strategies": [
            {"name": "rsi", "bucket": "momentum",
             "params": {"window": 14, "oversold": 99.0}},
        ],
        "min_agreeing_buckets": 1,
        "buy_threshold": 0.01,
        "atr_window": 99,   # ensemble-level param — should NOT reach rsi
        "stop_distance_multiplier": 1.5,
        "risk_per_trade_pct": 0.01,
    })
    ctx = StrategyContext(symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params={})
    # If RSI received atr_window=99 instead of window=14 it would fail to produce a signal.
    intents = ensemble.on_bar(ctx)
    assert len(intents) == 1


# ---------------------------------------------------------------------------
# Plain Strategy (sma_crossover) mapping
# ---------------------------------------------------------------------------

def test_sma_crossover_as_sub_strategy():
    """sma_crossover is a plain Strategy; ensemble maps its intents to ±1.0."""
    n = max(_MIN_BARS, 60)
    bars = _trending_bars(n)
    ensemble = EnsembleStrategy({
        "strategies": [
            {"name": "sma_crossover", "bucket": "trend",
             "params": {"fast": 5, "slow": 20, "atr_window": 14, "risk_per_trade_pct": 0.005}},
        ],
        "min_agreeing_buckets": 1,
        "buy_threshold": 0.5,   # sma intent maps to 1.0, so this is guaranteed
        "sell_threshold": -0.5,
        "atr_window": 14,
        "stop_distance_multiplier": 1.5,
        "risk_per_trade_pct": 0.01,
    })
    # The key assertion: no exception is raised and the result is a valid list.
    ctx = StrategyContext(symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params={})
    intents = ensemble.on_bar(ctx)
    assert isinstance(intents, list)
    assert all(isinstance(i, Intent) for i in intents)


# ---------------------------------------------------------------------------
# Volume filter
# ---------------------------------------------------------------------------

def test_volume_filter_wired():
    """Ensemble with volume_filter does not raise and returns a list."""
    n = max(_MIN_BARS, 60)
    bars = [_bar(200.0 - i * 0.5, volume=300.0, idx=i) for i in range(n)]
    ensemble = EnsembleStrategy({
        "strategies": [
            {"name": "rsi", "bucket": "momentum",
             "params": {"window": 14, "oversold": 99.0}},
        ],
        "volume_filter": {"strategy": "volume_signal", "params": {"window": 10}, "multiplier_scale": 0.25},
        "min_agreeing_buckets": 1,
        "buy_threshold": 0.01,
        "atr_window": 14,
        "stop_distance_multiplier": 1.5,
        "risk_per_trade_pct": 0.01,
    })
    ctx = StrategyContext(symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params={})
    intents = ensemble.on_bar(ctx)
    assert isinstance(intents, list)
