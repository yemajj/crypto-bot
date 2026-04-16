"""Tests for VolumeSurgeBreakoutStrategy (VSBR).

Test philosophy: verify signal-gate logic using deliberately constructed bar
sequences with known outcomes.  Each test targets one behavioural invariant.

Key properties under test:
  - Insufficient history → no signal
  - Volume surge alone (no price breakout) → no entry
  - Price breakout alone (no volume surge) → no entry
  - Both conditions met → BUY intent with valid stop_price and qty
  - In position + close below trailing EMA → SELL intent
  - In position + close above trailing EMA → no signal (hold)
  - No re-entry while in position (entry branch skipped)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.core.types import Bar, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.vsbr import VolumeSurgeBreakoutStrategy

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

_DEFAULT_PARAMS = {
    "vol_window": 5,
    "vol_surge_mult": 2.0,
    "breakout_window": 5,
    "trail_ema_window": 5,
    "atr_window": 5,
    "stop_distance_multiplier": 1.5,
    "risk_per_trade_pct": 0.01,
    "max_position_notional_pct": 0.10,
}

_EQUITY = 10_000.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(
    close: float,
    volume: float = 100.0,
    idx: int = 0,
    symbol: str = "BTC/USDT",
) -> Bar:
    return Bar(
        symbol=symbol,
        timeframe="15m",
        ts_open=_T0 + timedelta(minutes=15 * idx),
        open=Decimal(str(round(close, 8))),
        high=Decimal(str(round(close * 1.002, 8))),
        low=Decimal(str(round(close * 0.998, 8))),
        close=Decimal(str(round(close, 8))),
        volume=Decimal(str(round(volume, 8))),
    )


def _flat_position() -> Position:
    return Position(symbol="BTC/USDT", qty=Decimal("0"), avg_price=Decimal("0"))


def _long_position(qty: float = 0.01) -> Position:
    return Position(
        symbol="BTC/USDT",
        qty=Decimal(str(qty)),
        avg_price=Decimal("40000"),
    )


def _ctx(bars: list[Bar], position: Position | None = None, params: dict | None = None) -> StrategyContext:
    return StrategyContext(
        symbol="BTC/USDT",
        history=bars,
        position=position or _flat_position(),
        equity=_EQUITY,
        params=params or _DEFAULT_PARAMS,
    )


def _make_bars(n: int, close: float = 40_000.0, volume: float = 100.0) -> list[Bar]:
    return [_bar(close=close, volume=volume, idx=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Insufficient history
# ---------------------------------------------------------------------------

def test_insufficient_history_returns_empty() -> None:
    """Fewer bars than min_bars requirement → no signal."""
    strategy = VolumeSurgeBreakoutStrategy()
    # min_bars = max(vol_window=5, breakout_window=5, trail_ema_window=5) + 1 = 6
    bars = _make_bars(5)
    intents = strategy.on_bar(_ctx(bars))
    assert intents == []


def test_exactly_min_bars_does_not_error() -> None:
    """Exactly min_bars bars does not raise; may or may not signal."""
    strategy = VolumeSurgeBreakoutStrategy()
    bars = _make_bars(6)
    # Should not raise regardless of signal outcome.
    strategy.on_bar(_ctx(bars))


# ---------------------------------------------------------------------------
# Entry gate: both conditions required
# ---------------------------------------------------------------------------

def test_no_entry_without_volume_surge() -> None:
    """Price breakout alone (no volume spike) must not trigger entry."""
    strategy = VolumeSurgeBreakoutStrategy()
    # Flat bars at close=100, normal volume=100.
    # Final bar: close=101 (new high) but volume=100 (not a surge).
    bars = _make_bars(6, close=100.0, volume=100.0)
    bars[-1] = _bar(close=101.0, volume=100.0, idx=6)  # breakout close, no vol surge
    intents = strategy.on_bar(_ctx(bars))
    assert not any(i.side == Side.BUY for i in intents)


def test_no_entry_without_price_breakout() -> None:
    """Volume surge alone (no price breakout) must not trigger entry."""
    strategy = VolumeSurgeBreakoutStrategy()
    # Bars at close=100 with normal volume=100.
    # Final bar: big volume but close is NOT a new high (same as prior).
    bars = _make_bars(6, close=100.0, volume=100.0)
    bars[-1] = _bar(close=99.0, volume=300.0, idx=6)  # surge volume, no new high
    intents = strategy.on_bar(_ctx(bars))
    assert not any(i.side == Side.BUY for i in intents)


def test_entry_fires_on_both_conditions() -> None:
    """BUY intent fired when volume surge AND price breakout both present."""
    strategy = VolumeSurgeBreakoutStrategy()
    # 6 baseline bars: close=100, volume=100.
    bars = _make_bars(7, close=100.0, volume=100.0)
    # Replace final bar: new high close AND big volume surge (3× avg).
    bars[-1] = _bar(close=105.0, volume=300.0, idx=7)
    intents = strategy.on_bar(_ctx(bars))
    buys = [i for i in intents if i.side == Side.BUY]
    assert len(buys) == 1
    b = buys[0]
    assert b.stop_price is not None
    assert b.stop_price > Decimal("0")
    assert b.qty > Decimal("0")


def test_entry_qty_respects_max_notional() -> None:
    """Position size is capped by max_position_notional_pct."""
    strategy = VolumeSurgeBreakoutStrategy()
    bars = _make_bars(7, close=100.0, volume=100.0)
    bars[-1] = _bar(close=105.0, volume=300.0, idx=7)
    params = {**_DEFAULT_PARAMS, "max_position_notional_pct": 0.05}
    intents = strategy.on_bar(_ctx(bars, params=params))
    buys = [i for i in intents if i.side == Side.BUY]
    assert buys
    max_qty_allowed = _EQUITY * 0.05 / 105.0
    assert float(buys[0].qty) <= max_qty_allowed + 1e-9


def test_stop_price_below_entry_close() -> None:
    """stop_price must be strictly below the entry close."""
    strategy = VolumeSurgeBreakoutStrategy()
    bars = _make_bars(7, close=100.0, volume=100.0)
    bars[-1] = _bar(close=105.0, volume=300.0, idx=7)
    intents = strategy.on_bar(_ctx(bars))
    buys = [i for i in intents if i.side == Side.BUY]
    assert buys
    assert buys[0].stop_price < Decimal("105.0")


# ---------------------------------------------------------------------------
# Exit: trailing EMA
# ---------------------------------------------------------------------------

def test_exit_fires_when_close_below_ema() -> None:
    """SELL intent issued when in position and close < trailing EMA."""
    strategy = VolumeSurgeBreakoutStrategy()
    # Construct bars that produce a high EMA, then end with a low close.
    # trail_ema_window=5; seed with 5 bars at close=200, then 1 bar at close=50.
    bars = _make_bars(10, close=200.0, volume=100.0)
    bars[-1] = _bar(close=50.0, volume=100.0, idx=10)  # big drop below EMA
    intents = strategy.on_bar(_ctx(bars, position=_long_position()))
    sells = [i for i in intents if i.side == Side.SELL]
    assert len(sells) == 1
    assert sells[0].qty > Decimal("0")


def test_hold_when_close_above_ema() -> None:
    """No signal when in position and close is above trailing EMA."""
    strategy = VolumeSurgeBreakoutStrategy()
    # Steadily rising bars — EMA tracks below close, so no exit.
    bars = [_bar(close=100.0 + i * 2, volume=100.0, idx=i) for i in range(10)]
    intents = strategy.on_bar(_ctx(bars, position=_long_position()))
    assert intents == []


# ---------------------------------------------------------------------------
# No re-entry while in position
# ---------------------------------------------------------------------------

def test_no_entry_while_in_position() -> None:
    """BUY gate skipped entirely when a position is already open."""
    strategy = VolumeSurgeBreakoutStrategy()
    # Construct a bar that would trigger entry if flat.
    bars = _make_bars(7, close=100.0, volume=100.0)
    bars[-1] = _bar(close=105.0, volume=300.0, idx=7)
    # But also make close > EMA so exit does not fire (steadily rising).
    bars = [_bar(close=100.0 + i, volume=100.0, idx=i) for i in range(7)]
    bars[-1] = _bar(close=200.0, volume=300.0, idx=7)  # big vol, new high, rising EMA
    intents = strategy.on_bar(_ctx(bars, position=_long_position()))
    buys = [i for i in intents if i.side == Side.BUY]
    assert buys == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_zero_average_volume_no_entry() -> None:
    """avg_vol == 0 must not cause division error or spurious entry."""
    strategy = VolumeSurgeBreakoutStrategy()
    bars = _make_bars(7, close=100.0, volume=0.0)
    bars[-1] = _bar(close=105.0, volume=0.0, idx=7)
    intents = strategy.on_bar(_ctx(bars))
    assert not any(i.side == Side.BUY for i in intents)


def test_intent_strategy_id() -> None:
    """Emitted intents carry the correct strategy_id."""
    strategy = VolumeSurgeBreakoutStrategy()
    bars = _make_bars(7, close=100.0, volume=100.0)
    bars[-1] = _bar(close=105.0, volume=300.0, idx=7)
    intents = strategy.on_bar(_ctx(bars))
    for intent in intents:
        assert intent.strategy_id == "vsbr"
