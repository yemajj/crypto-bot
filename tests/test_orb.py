"""Tests for ORBStrategy (Opening Range Breakout).

Test cases cover:
  - warmup guard (too few bars)
  - range still forming (not enough session bars before current)
  - breakout above orb_high → +1.0
  - breakdown below orb_low  → -1.0
  - inside range             →  0.0
  - entry cutoff (max_entry_bar)
  - session isolation (prior-day bars excluded from range)
  - range computed from first orb_bars only (later session highs ignored)
  - volume confirmation filter (on and off)
  - on_bar() integration (BUY intent on breakout)
  - registry registration
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.core.types import Bar, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.orb import ORBStrategy, _session_bars_before

_SYMBOL = "BTC/USDT"
_TF = "1h"
_FLAT = Position(symbol=_SYMBOL, qty=Decimal("0"), avg_price=Decimal("0"))

# Two fixed UTC days used throughout the tests.
_DAY1 = datetime(2024, 1, 1, tzinfo=timezone.utc)   # 2024-01-01 00:00 UTC
_DAY2 = datetime(2024, 1, 2, tzinfo=timezone.utc)   # 2024-01-02 00:00 UTC


def _bar(
    ts: datetime,
    close: float,
    high: float | None = None,
    low: float | None = None,
    volume: float = 100.0,
) -> Bar:
    h = high if high is not None else close + 50.0
    lo = low if low is not None else close - 50.0
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=ts,
        open=Decimal(str(close)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
    )


def _day_bars(day_start: datetime, n: int, close: float = 40_000.0) -> list[Bar]:
    """Return n consecutive 1h bars starting at day_start."""
    return [_bar(day_start + timedelta(hours=i), close) for i in range(n)]


def _ctx(bars: list[Bar], params: dict | None = None) -> StrategyContext:
    defaults = {
        "orb_bars": 2,
        "max_entry_bar": 16,
        "atr_window": 3,
        "risk_per_trade_pct": 0.01,
        "stop_distance_multiplier": 1.5,
        "buy_threshold": 0.5,
        "sell_threshold": -0.5,
    }
    p = {**defaults, **(params or {})}
    return StrategyContext(
        symbol=_SYMBOL,
        history=bars,
        position=_FLAT,
        equity=10_000.0,
        params=p,
    )


# ---------------------------------------------------------------------------
# Warmup / insufficient data
# ---------------------------------------------------------------------------

def test_single_bar_returns_zero():
    strat = ORBStrategy()
    bars = [_bar(_DAY1, 40_000.0)]
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_range_still_forming_first_bar_of_session():
    """First bar of a session: no prior session bars → range not yet formed."""
    strat = ORBStrategy()
    # History has previous-day bars + the first bar of the new day.
    prev = _day_bars(_DAY1, 24, close=40_000.0)
    current = _bar(_DAY2, 40_000.0)
    bars = prev + [current]
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_range_still_forming_second_bar_of_session():
    """With orb_bars=2: after only 1 session bar, range is still forming."""
    strat = ORBStrategy()
    prev = _day_bars(_DAY1, 24, close=40_000.0)
    session_bar_0 = _bar(_DAY2, 40_000.0)
    current = _bar(_DAY2 + timedelta(hours=1), 40_000.0)
    bars = prev + [session_bar_0, current]
    # session_bars before current = [session_bar_0], len=1 < orb_bars=2
    assert strat.signal_score(_ctx(bars)) == 0.0


# ---------------------------------------------------------------------------
# Breakout / breakdown / inside
# ---------------------------------------------------------------------------

def _orb_setup(
    orb_bar_highs: list[float],
    orb_bar_lows: list[float],
    current_close: float,
    bar_position: int = 2,   # number of session bars before current
) -> list[Bar]:
    """Build a minimal history for ORB tests.

    Previous day: 24 bars at 40_000 (neutral).
    Current day opening range: len(orb_bar_highs) bars.
    Filler session bars: enough to reach bar_position.
    Current bar: one bar with current_close.
    """
    prev = _day_bars(_DAY1, 24, close=40_000.0)
    orb_bars_count = len(orb_bar_highs)
    session: list[Bar] = []
    for i, (h, lo) in enumerate(zip(orb_bar_highs, orb_bar_lows)):
        session.append(_bar(_DAY2 + timedelta(hours=i), (h + lo) / 2, high=h, low=lo))
    # Filler bars between end of opening range and current bar.
    for i in range(orb_bars_count, bar_position):
        session.append(_bar(_DAY2 + timedelta(hours=i), 40_000.0))
    current = _bar(_DAY2 + timedelta(hours=bar_position), current_close)
    return prev + session + [current]


def test_breakout_above_orb_high():
    strat = ORBStrategy()
    # Opening range: highs=[41_000, 41_500], lows=[39_000, 39_500]
    # orb_high = 41_500; current close = 42_000 → breakout
    bars = _orb_setup([41_000, 41_500], [39_000, 39_500], current_close=42_000.0)
    assert strat.signal_score(_ctx(bars)) == 1.0


def test_breakdown_below_orb_low():
    strat = ORBStrategy()
    # orb_low = 39_000; current close = 38_000 → breakdown
    bars = _orb_setup([41_000, 41_500], [39_000, 39_500], current_close=38_000.0)
    assert strat.signal_score(_ctx(bars)) == -1.0


def test_inside_range_returns_zero():
    strat = ORBStrategy()
    # current close = 40_000 — inside [39_000, 41_500]
    bars = _orb_setup([41_000, 41_500], [39_000, 39_500], current_close=40_000.0)
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_exactly_at_orb_high_is_inside():
    """close == orb_high is not a breakout (strict >)."""
    strat = ORBStrategy()
    bars = _orb_setup([41_000, 41_500], [39_000, 39_500], current_close=41_500.0)
    assert strat.signal_score(_ctx(bars)) == 0.0


def test_exactly_at_orb_low_is_inside():
    """close == orb_low is not a breakdown (strict <)."""
    strat = ORBStrategy()
    bars = _orb_setup([41_000, 41_500], [39_000, 39_500], current_close=39_000.0)
    assert strat.signal_score(_ctx(bars)) == 0.0


# ---------------------------------------------------------------------------
# Entry cutoff (max_entry_bar)
# ---------------------------------------------------------------------------

def test_max_entry_bar_cutoff():
    """No signal returned when bar_position > max_entry_bar."""
    strat = ORBStrategy()
    # bar_position=17 > max_entry_bar=16 → return 0.0 even though it's a breakout
    bars = _orb_setup([41_000, 41_500], [39_000, 39_500], current_close=42_000.0, bar_position=17)
    assert strat.signal_score(_ctx(bars, {"orb_bars": 2, "max_entry_bar": 16})) == 0.0


def test_exactly_at_max_entry_bar_still_signals():
    """bar_position == max_entry_bar is still within the entry window."""
    strat = ORBStrategy()
    bars = _orb_setup([41_000, 41_500], [39_000, 39_500], current_close=42_000.0, bar_position=16)
    assert strat.signal_score(_ctx(bars, {"orb_bars": 2, "max_entry_bar": 16})) == 1.0


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------

def test_previous_day_bars_not_included_in_range():
    """Prior-day bars with extreme highs/lows must not contaminate the ORB range."""
    strat = ORBStrategy()
    # Previous day has extreme prices that would dominate if included.
    prev = [_bar(_DAY1 + timedelta(hours=i), 50_000.0, high=60_000.0, low=30_000.0) for i in range(24)]
    # Current day opening range is tight around 40_000.
    orb_0 = _bar(_DAY2, 40_000.0, high=40_500.0, low=39_500.0)
    orb_1 = _bar(_DAY2 + timedelta(hours=1), 40_000.0, high=40_500.0, low=39_500.0)
    # Breakout bar: close=41_000 > orb_high=40_500 → +1.0
    current = _bar(_DAY2 + timedelta(hours=2), 41_000.0)
    bars = prev + [orb_0, orb_1, current]
    assert strat.signal_score(_ctx(bars)) == 1.0


def test_range_uses_only_first_orb_bars():
    """Later session bars with higher highs must not expand the opening range."""
    strat = ORBStrategy()
    prev = _day_bars(_DAY1, 24, close=40_000.0)
    # Opening range (bars 0 & 1): high=41_000, low=39_000
    orb_0 = _bar(_DAY2, 40_000.0, high=41_000.0, low=39_000.0)
    orb_1 = _bar(_DAY2 + timedelta(hours=1), 40_000.0, high=41_000.0, low=39_000.0)
    # Bar 2 has a much higher high — but it's after the ORB window.
    bar_2 = _bar(_DAY2 + timedelta(hours=2), 40_000.0, high=50_000.0, low=39_000.0)
    # Current bar (bar 3): close=41_500 > orb_high=41_000 → should be +1.0
    current = _bar(_DAY2 + timedelta(hours=3), 41_500.0)
    bars = prev + [orb_0, orb_1, bar_2, current]
    assert strat.signal_score(_ctx(bars)) == 1.0


# ---------------------------------------------------------------------------
# Custom orb_bars parameter
# ---------------------------------------------------------------------------

def test_orb_bars_1_uses_single_bar_range():
    strat = ORBStrategy()
    prev = _day_bars(_DAY1, 24, close=40_000.0)
    orb_0 = _bar(_DAY2, 40_000.0, high=41_000.0, low=39_000.0)
    current = _bar(_DAY2 + timedelta(hours=1), 41_500.0)
    bars = prev + [orb_0, current]
    # orb_bars=1 → range formed from bar 0 alone; close=41_500 > high=41_000 → +1.0
    assert strat.signal_score(_ctx(bars, {"orb_bars": 1, "max_entry_bar": 16})) == 1.0


# ---------------------------------------------------------------------------
# Volume confirmation
# ---------------------------------------------------------------------------

def test_volume_confirm_blocks_low_volume_breakout():
    strat = ORBStrategy()
    prev = [_bar(_DAY1 + timedelta(hours=i), 40_000.0, volume=200.0) for i in range(24)]
    orb_0 = _bar(_DAY2, 40_000.0, high=41_000.0, low=39_000.0, volume=200.0)
    orb_1 = _bar(_DAY2 + timedelta(hours=1), 40_000.0, high=41_000.0, low=39_000.0, volume=200.0)
    # Breakout bar with LOW volume (50 vs avg ~200) — should be blocked.
    current = _bar(_DAY2 + timedelta(hours=2), 42_000.0, volume=50.0)
    bars = prev + [orb_0, orb_1, current]
    params = {"orb_bars": 2, "max_entry_bar": 16, "volume_confirm": True, "volume_window": 20}
    assert strat.signal_score(_ctx(bars, params)) == 0.0


def test_volume_confirm_allows_high_volume_breakout():
    strat = ORBStrategy()
    prev = [_bar(_DAY1 + timedelta(hours=i), 40_000.0, volume=200.0) for i in range(24)]
    orb_0 = _bar(_DAY2, 40_000.0, high=41_000.0, low=39_000.0, volume=200.0)
    orb_1 = _bar(_DAY2 + timedelta(hours=1), 40_000.0, high=41_000.0, low=39_000.0, volume=200.0)
    # Breakout bar with HIGH volume (500 > avg ~200) — should signal.
    current = _bar(_DAY2 + timedelta(hours=2), 42_000.0, volume=500.0)
    bars = prev + [orb_0, orb_1, current]
    params = {"orb_bars": 2, "max_entry_bar": 16, "volume_confirm": True, "volume_window": 20}
    assert strat.signal_score(_ctx(bars, params)) == 1.0


def test_volume_confirm_false_does_not_filter():
    """volume_confirm=False must pass through regardless of volume."""
    strat = ORBStrategy()
    prev = [_bar(_DAY1 + timedelta(hours=i), 40_000.0, volume=200.0) for i in range(24)]
    orb_0 = _bar(_DAY2, 40_000.0, high=41_000.0, low=39_000.0, volume=200.0)
    orb_1 = _bar(_DAY2 + timedelta(hours=1), 40_000.0, high=41_000.0, low=39_000.0, volume=200.0)
    current = _bar(_DAY2 + timedelta(hours=2), 42_000.0, volume=1.0)  # trivial volume
    bars = prev + [orb_0, orb_1, current]
    params = {"orb_bars": 2, "max_entry_bar": 16, "volume_confirm": False}
    assert strat.signal_score(_ctx(bars, params)) == 1.0


# ---------------------------------------------------------------------------
# _session_bars_before helper
# ---------------------------------------------------------------------------

def test_session_bars_before_excludes_same_ts():
    """Bars with the same timestamp as current are not included."""
    current = _bar(_DAY2, 40_000.0)
    same_ts = _bar(_DAY2, 39_000.0)
    assert _session_bars_before([same_ts, current], current) == []


def test_session_bars_before_excludes_previous_day():
    prev = _bar(_DAY1 + timedelta(hours=23), 40_000.0)
    session_bar = _bar(_DAY2, 40_000.0)
    current = _bar(_DAY2 + timedelta(hours=1), 40_000.0)
    result = _session_bars_before([prev, session_bar, current], current)
    assert result == [session_bar]


# ---------------------------------------------------------------------------
# on_bar() integration
# ---------------------------------------------------------------------------

def test_on_bar_generates_buy_intent_on_breakout():
    strat = ORBStrategy()
    prev = _day_bars(_DAY1, 24, close=40_000.0)
    orb_0 = _bar(_DAY2, 40_000.0, high=41_000.0, low=39_000.0)
    orb_1 = _bar(_DAY2 + timedelta(hours=1), 40_000.0, high=41_000.0, low=39_000.0)
    current = _bar(_DAY2 + timedelta(hours=2), 42_000.0)
    bars = prev + [orb_0, orb_1, current]
    ctx = _ctx(bars, {"orb_bars": 2, "max_entry_bar": 16, "atr_window": 3,
                      "risk_per_trade_pct": 0.01, "buy_threshold": 0.5})
    intents = strat.on_bar(ctx)
    assert len(intents) == 1
    assert intents[0].side == Side.BUY
    assert intents[0].stop_price is not None


def test_on_bar_no_intent_when_inside_range():
    strat = ORBStrategy()
    prev = _day_bars(_DAY1, 24, close=40_000.0)
    orb_0 = _bar(_DAY2, 40_000.0, high=41_000.0, low=39_000.0)
    orb_1 = _bar(_DAY2 + timedelta(hours=1), 40_000.0, high=41_000.0, low=39_000.0)
    current = _bar(_DAY2 + timedelta(hours=2), 40_000.0)
    bars = prev + [orb_0, orb_1, current]
    intents = strat.on_bar(_ctx(bars))
    assert intents == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registered_as_orb():
    from cryptobot.strategy.registry import get_strategy
    assert get_strategy("orb") is ORBStrategy
