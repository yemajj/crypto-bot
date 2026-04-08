"""Tests for RsiStrategy and _rsi helper."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.core.types import Bar, Intent, OrderType, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.rsi import RsiStrategy, _rsi

_SYMBOL = "BTC/USDT"
_TF = "1h"
_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_FLAT_POSITION = Position(symbol=_SYMBOL, qty=Decimal("0"), avg_price=Decimal("0"))
_LONG_POSITION = Position(symbol=_SYMBOL, qty=Decimal("0.1"), avg_price=Decimal("100"))


def _bar(close: float, idx: int = 0) -> Bar:
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_T0 + timedelta(hours=idx),
        open=Decimal(str(close)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 1)),
        close=Decimal(str(close)),
        volume=Decimal("1"),
    )


def _ctx(history: list[Bar], position=_FLAT_POSITION, equity=10_000.0, params=None) -> StrategyContext:
    return StrategyContext(
        symbol=_SYMBOL,
        history=history,
        position=position,
        equity=equity,
        params=params or {},
    )


# ---------------------------------------------------------------------------
# _rsi helper
# ---------------------------------------------------------------------------

def test_rsi_insufficient_data_returns_neutral():
    closes = [100.0] * 14   # need window+1 = 15
    assert _rsi(closes, window=14) == 50.0


def test_rsi_all_gains_returns_100():
    # Strictly rising series — avg_loss == 0
    closes = [float(i) for i in range(16)]
    assert _rsi(closes, window=14) == 100.0


def test_rsi_all_losses_returns_0():
    # Strictly falling series — avg_gain == 0
    closes = [float(16 - i) for i in range(16)]
    assert _rsi(closes, window=14) == 0.0


def test_rsi_neutral_alternating():
    # Alternating +1/-1: roughly equal gains/losses → RSI near 50
    closes = [100.0 + (1 if i % 2 == 0 else -1) for i in range(16)]
    rsi = _rsi(closes, window=14)
    assert 40.0 <= rsi <= 60.0


# ---------------------------------------------------------------------------
# RsiStrategy.signal_score
# ---------------------------------------------------------------------------

def test_signal_score_too_few_bars_returns_zero():
    strat = RsiStrategy()
    bars = [_bar(100.0, i) for i in range(10)]   # fewer than window+1=15
    assert strat.signal_score(_ctx(bars, params={"window": 14})) == 0.0


def test_signal_score_neutral_zone_returns_zero():
    strat = RsiStrategy()
    # RSI will be ~50 with alternating prices
    closes = [100.0 + (1 if i % 2 == 0 else -1) for i in range(16)]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    score = strat.signal_score(_ctx(bars, params={"window": 14}))
    assert score == 0.0


def test_signal_score_declining_prices_gives_positive_score():
    strat = RsiStrategy()
    # Strictly falling → RSI=0 → score = oversold/oversold = 1.0
    closes = [float(100 - i) for i in range(16)]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    score = strat.signal_score(_ctx(bars, params={"window": 14, "oversold": 30.0}))
    assert score > 0.0
    assert score <= 1.0


def test_signal_score_rising_prices_gives_negative_score():
    strat = RsiStrategy()
    # Strictly rising → RSI=100 → score = -(100-overbought)/(100-overbought) = -1.0
    closes = [float(100 + i) for i in range(16)]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    score = strat.signal_score(_ctx(bars, params={"window": 14, "overbought": 70.0}))
    assert score < 0.0
    assert score >= -1.0


def test_signal_score_respects_custom_overbought_threshold():
    strat = RsiStrategy()
    # Alternating series gives RSI ≈ 50, so it is below overbought=70 but
    # below overbought=99.9 too → both should return 0.0 (neutral zone).
    # Use a mostly-rising series where RSI lands around 70–80 to exercise
    # the threshold boundary: with overbought=65 it triggers, with 85 it doesn't.
    # 14 gains of 1.0, 1 loss of 0.1 → avg_gain≈0.929, avg_loss≈0.007 → RSI≈99
    gains = [100.0 + i for i in range(14)]  # 14 rising bars (gives 15 closes total)
    closes = gains + [gains[-1] - 0.1]       # 1 small dip at end (window=14, need 15 values)
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    # overbought=70: RSI≈99 > 70 → score negative
    # overbought=99.9: RSI≈99 < 99.9 → neutral zone → 0.0
    score_loose = strat.signal_score(_ctx(bars, params={"window": 14, "overbought": 70.0}))
    score_tight = strat.signal_score(_ctx(bars, params={"window": 14, "overbought": 99.9}))
    assert score_loose < 0.0        # triggers with loose threshold
    assert score_tight == 0.0       # neutral with tight threshold


# ---------------------------------------------------------------------------
# RsiStrategy.on_bar (via ScoringStrategy default)
# ---------------------------------------------------------------------------

def test_on_bar_buy_intent_when_oversold():
    strat = RsiStrategy()
    closes = [float(100 - i) for i in range(16)]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    params = {"window": 14, "oversold": 30.0, "atr_window": 5, "risk_per_trade_pct": 0.01}
    intents = strat.on_bar(_ctx(bars, params=params))
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == Side.BUY
    assert intent.stop_price is not None
    assert intent.order_type == OrderType.MARKET


def test_on_bar_no_intent_in_neutral_zone():
    strat = RsiStrategy()
    closes = [100.0 + (1 if i % 2 == 0 else -1) for i in range(16)]
    bars = [_bar(c, i) for i, c in enumerate(closes)]
    intents = strat.on_bar(_ctx(bars, params={"window": 14}))
    assert intents == []


def test_registered_as_rsi():
    from cryptobot.strategy.registry import get_strategy
    cls = get_strategy("rsi")
    assert cls is RsiStrategy
