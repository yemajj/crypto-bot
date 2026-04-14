"""Tests for DonchianAdxStrategy.

Design notes for the ADX-gate tests
-------------------------------------
We use adx_window=5 (needs 2*5+1=11 bars) and window=5 (needs 6 bars),
so the combined minimum is 11 bars.

High-ADX scenario  — monotonically-increasing bars (+1 per bar).  With a
  perfect uptrend every +DM=1, -DM=0, so DX≈100 at every post-seed step and
  ADX converges to ~100.

Low-ADX scenario  — 10 flat bars followed by a single breakout bar.  The flat
  bars produce DX=0; the breakout bar spikes DX to ~100 but only contributes
  1/5 to the Wilder-seeded ADX, leaving ADX≈20, below the 25 threshold.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cryptobot.core.types import Bar, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.donchian_adx import DonchianAdxStrategy

_SYMBOL = "BTC/USDT"
_TF = "15m"
_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_FLAT = Position(symbol=_SYMBOL, qty=Decimal("0"), avg_price=Decimal("0"))


def _bar(close: float, high: float | None = None, low: float | None = None, idx: int = 0) -> Bar:
    h = high if high is not None else close + 1
    l = low if low is not None else close - 1
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_T0 + timedelta(minutes=15 * idx),
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
        params=params or {"window": 5, "adx_window": 5, "adx_threshold": 25.0},
    )


# ---------------------------------------------------------------------------
# Basic sanity
# ---------------------------------------------------------------------------

def test_too_few_bars_returns_zero():
    """ADX gate active: need max(window+1, 2*adx_window+1) = max(6,11) = 11 bars."""
    strat = DonchianAdxStrategy()
    bars = [_bar(100.0, idx=i) for i in range(10)]  # one short
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_inside_channel_returns_zero():
    """Close inside the channel always scores 0, regardless of ADX."""
    strat = DonchianAdxStrategy()
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(10)]
    current = _bar(100.0, high=101.0, low=99.0, idx=10)
    bars = prior + [current]
    assert strat.signal_score(_ctx(bars)) == 0.0


# ---------------------------------------------------------------------------
# ADX gate disabled (threshold = 0)
# ---------------------------------------------------------------------------

def test_threshold_zero_disables_gate():
    """adx_threshold=0 disables the ADX check; only window+1 bars needed."""
    strat = DonchianAdxStrategy()
    # window=5 → need 6 bars; breakout on bar 5
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(5)]
    current = _bar(102.0, high=103.0, low=101.0, idx=5)
    bars = prior + [current]
    params = {"window": 5, "adx_window": 5, "adx_threshold": 0.0}
    assert strat.signal_score(_ctx(bars, params)) == 1.0


def test_threshold_zero_breakdown_fires():
    strat = DonchianAdxStrategy()
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(5)]
    current = _bar(97.0, high=98.0, low=96.0, idx=5)
    bars = prior + [current]
    params = {"window": 5, "adx_window": 5, "adx_threshold": 0.0}
    assert strat.signal_score(_ctx(bars, params)) == -1.0


# ---------------------------------------------------------------------------
# ADX gate active — blocking case
# ---------------------------------------------------------------------------

def test_adx_gate_blocks_flat_market_breakout():
    """Flat prior bars yield ADX≈20, which is below threshold=25 → blocked.

    10 flat bars at 100, then a breakout bar at 102.
    Wilder ADX seeds on DX=0 for the first four post-seed steps then gets one
    DX≈100 contribution from the breakout bar → ADX ≈ 20.
    """
    strat = DonchianAdxStrategy()
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(10)]
    current = _bar(102.0, high=103.0, low=101.0, idx=10)
    bars = prior + [current]
    # threshold=25 > ADX≈20 → gate blocks
    params = {"window": 5, "adx_window": 5, "adx_threshold": 25.0}
    assert strat.signal_score(_ctx(bars, params)) == 0.0


# ---------------------------------------------------------------------------
# ADX gate active — allowing case
# ---------------------------------------------------------------------------

def test_adx_gate_allows_trending_market_breakout():
    """Monotonically-increasing bars yield ADX≈100, which passes threshold=25.

    Bars go up 1 per step: close 90→99 over 10 bars, then breakout at 102
    (above prior 5-bar high of 100).
    """
    strat = DonchianAdxStrategy()
    # 10 trending prior bars + 1 breakout bar = 11 total (minimum for adx_window=5)
    bars = [_bar(90.0 + i, high=91.0 + i, low=89.0 + i, idx=i) for i in range(10)]
    current = _bar(102.0, high=103.0, low=101.0, idx=10)
    bars = bars + [current]
    # channel from prior 5 bars (bars[5..9]): highs = 96,97,98,99,100 → high=100
    # close=102 > 100 → breakout; ADX≈100 >> 25 → gate allows
    params = {"window": 5, "adx_window": 5, "adx_threshold": 25.0}
    assert strat.signal_score(_ctx(bars, params)) == 1.0


def test_adx_gate_allows_trending_breakdown():
    """Monotonically-decreasing bars yield high ADX; breakdown fires."""
    strat = DonchianAdxStrategy()
    bars = [_bar(100.0 - i, high=101.0 - i, low=99.0 - i, idx=i) for i in range(10)]
    current = _bar(88.0, high=89.0, low=87.0, idx=10)
    bars = bars + [current]
    # prior 5 bars lows = 94,93,92,91,90 → low=90; close=88 < 90 → breakdown
    params = {"window": 5, "adx_window": 5, "adx_threshold": 25.0}
    assert strat.signal_score(_ctx(bars, params)) == -1.0


# ---------------------------------------------------------------------------
# Integration: on_bar produces correct Intent
# ---------------------------------------------------------------------------

def test_on_bar_generates_buy_on_trending_breakout():
    strat = DonchianAdxStrategy()
    bars = [_bar(90.0 + i, high=91.0 + i, low=89.0 + i, idx=i) for i in range(10)]
    current = _bar(102.0, high=103.0, low=101.0, idx=10)
    bars = bars + [current]
    params = {
        "window": 5,
        "adx_window": 5,
        "adx_threshold": 25.0,
        "atr_window": 3,
        "risk_per_trade_pct": 0.01,
        "buy_threshold": 0.5,
    }
    ctx = StrategyContext(
        symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params=params
    )
    intents = strat.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].side == Side.BUY
    assert intents[0].stop_price is not None


def test_on_bar_suppressed_by_adx_gate():
    """on_bar must produce no intent when ADX gate blocks the breakout."""
    strat = DonchianAdxStrategy()
    prior = [_bar(100.0, high=101.0, low=99.0, idx=i) for i in range(10)]
    current = _bar(102.0, high=103.0, low=101.0, idx=10)
    bars = prior + [current]
    params = {
        "window": 5,
        "adx_window": 5,
        "adx_threshold": 25.0,
        "atr_window": 3,
        "risk_per_trade_pct": 0.01,
        "buy_threshold": 0.5,
    }
    ctx = StrategyContext(
        symbol=_SYMBOL, history=bars, position=_FLAT, equity=10_000.0, params=params
    )
    assert strat.on_bar(ctx) == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registered_as_donchian_adx():
    from cryptobot.strategy.registry import get_strategy
    assert get_strategy("donchian_adx") is DonchianAdxStrategy
