"""Tests for VolatilityRegimeMeanReversionStrategy (VRFMR).

Test philosophy: verify the signal-gate logic in isolation using deliberately
constructed bar sequences with known indicator outcomes.

Bar-construction notes (adx_window=14, rsi_window=14 throughout):
  - Flat bars (all same close):
      ADX = 0 (±DM = 0 every bar)
      RSI = 100 (avg_loss = 0, _rsi returns 100)
  - 30 flat + 3 declining (−2 % each):
      ADX ≈ 19.9 < 25  (three DX≈100 spikes Wilder-smoothed over quiet base)
      RSI = 0          (no gains in the 14-bar window; three losses dominate)
  - 35 strongly declining (−3 % per bar):
      ADX >> 25 (consistently directional — Wilder ADX saturates high)
      RSI = 0   (no gains anywhere in history)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cryptobot.core.types import Bar, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.vrfmr import VolatilityRegimeMeanReversionStrategy

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

_DEFAULT_PARAMS = {
    "adx_window": 14,
    "adx_threshold": 25.0,
    "rsi_window": 14,
    "rsi_oversold": 30.0,
    "rsi_exit": 50.0,
    "atr_window": 5,
    "stop_distance_multiplier": 1.5,
    "risk_per_trade_pct": 0.005,
    "max_position_notional_pct": 0.08,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(symbol: str = "BTC/USDT", close: float = 40_000.0, idx: int = 0) -> Bar:
    return Bar(
        symbol=symbol,
        timeframe="1h",
        ts_open=_T0 + timedelta(hours=idx),
        open=Decimal(str(round(close, 8))),
        high=Decimal(str(round(close * 1.005, 8))),
        low=Decimal(str(round(close * 0.995, 8))),
        close=Decimal(str(round(close, 8))),
        volume=Decimal("100"),
    )


def _bars_flat(symbol: str, close: float, n: int) -> list[Bar]:
    """n identical bars → ADX = 0, RSI = 100 (avg_loss = 0)."""
    return [_bar(symbol, close, idx=i) for i in range(n)]


def _bars_trending(symbol: str, start_close: float, pct_per_bar: float, n: int) -> list[Bar]:
    """n bars each moving pct_per_bar % from the previous close.

    Use negative pct_per_bar for a downtrend.
    A strong consistent trend (|pct| >= 1.5) produces ADX > 25 in < 20 bars.
    """
    bars: list[Bar] = []
    close = start_close
    for i in range(n):
        bars.append(_bar(symbol, close, idx=i))
        close = close * (1 + pct_per_bar / 100.0)
    return bars


def _flat_then_decline(
    symbol: str,
    base_close: float,
    n_flat: int,
    n_decline: int,
    decline_pct: float = 2.0,
) -> list[Bar]:
    """n_flat flat bars followed by n_decline bars each declining decline_pct %.

    With n_flat=30, n_decline=3, adx_window=14:
      ADX ≈ 19.9 < 25  (three DX=100 spikes after a long quiet seed)
      RSI = 0           (no gains in the last 14 bars)
    """
    bars = _bars_flat(symbol, base_close, n_flat)
    close = base_close
    for i in range(n_decline):
        close = close * (1 - decline_pct / 100.0)
        bars.append(_bar(symbol, close, idx=n_flat + i))
    return bars


def _ctx(
    symbol: str,
    bars: list[Bar],
    position: Position | None = None,
    equity: float = 10_000.0,
    params: dict | None = None,
) -> StrategyContext:
    pos = position or Position(symbol=symbol, qty=Decimal("0"), avg_price=Decimal("0"))
    return StrategyContext(
        symbol=symbol,
        history=bars,
        position=pos,
        equity=equity,
        params=params or _DEFAULT_PARAMS,
    )


def _long(symbol: str, price: float = 40_000.0) -> Position:
    return Position(symbol=symbol, qty=Decimal("0.01"), avg_price=Decimal(str(price)))


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------

def test_insufficient_bars_returns_empty():
    """Fewer than 2*adx_window+1 bars → no signal regardless of price action."""
    strat = VolatilityRegimeMeanReversionStrategy()
    # Need 29 bars; provide only 28.
    bars = _bars_flat("BTC/USDT", 40_000.0, 28)
    assert strat.on_bar(_ctx("BTC/USDT", bars)) == []


# ---------------------------------------------------------------------------
# Regime gate: trending market blocks entry
# ---------------------------------------------------------------------------

def test_trending_regime_blocks_entry():
    """High ADX blocks BUY even when RSI is oversold.

    Strong downtrend → ADX >> 25 (trending), RSI → 0 (oversold).
    The regime gate must prevent entry.
    """
    strat = VolatilityRegimeMeanReversionStrategy()
    # 35 bars of −3 % per bar: ADX saturates well above 25; RSI = 0.
    bars = _bars_trending("BTC/USDT", 40_000.0, pct_per_bar=-3.0, n=35)
    intents = strat.on_bar(_ctx("BTC/USDT", bars))
    assert intents == []


# ---------------------------------------------------------------------------
# Entry signals
# ---------------------------------------------------------------------------

def test_range_bound_oversold_generates_buy():
    """Low ADX + oversold RSI → BUY intent.

    30 flat bars seed ADX near 0; 3 declining bars push RSI to 0 while
    Wilder-smoothed ADX stays ≈ 19.9 — below the 25.0 threshold.
    """
    strat = VolatilityRegimeMeanReversionStrategy()
    bars = _flat_then_decline("BTC/USDT", 40_000.0, n_flat=30, n_decline=3)
    intents = strat.on_bar(_ctx("BTC/USDT", bars))
    assert len(intents) == 1
    assert intents[0].side == Side.BUY
    assert intents[0].symbol == "BTC/USDT"


def test_buy_intent_has_stop_price():
    """BUY intent must carry stop_price (RequireStopLoss compliance)."""
    strat = VolatilityRegimeMeanReversionStrategy()
    bars = _flat_then_decline("BTC/USDT", 40_000.0, n_flat=30, n_decline=3)
    intents = strat.on_bar(_ctx("BTC/USDT", bars))
    assert intents and intents[0].side == Side.BUY
    assert intents[0].stop_price is not None
    assert intents[0].stop_price > Decimal("0")


def test_range_bound_not_oversold_no_entry():
    """Range-bound regime but RSI not oversold → no signal.

    Flat bars: ADX = 0 (range-bound ✓), RSI = 100 (no losses → not oversold ✗).
    """
    strat = VolatilityRegimeMeanReversionStrategy()
    bars = _bars_flat("BTC/USDT", 40_000.0, 33)
    intents = strat.on_bar(_ctx("BTC/USDT", bars))
    assert intents == []


# ---------------------------------------------------------------------------
# Exit signals
# ---------------------------------------------------------------------------

def test_exit_on_rsi_recovery():
    """Has position + RSI recovered above rsi_exit → SELL.

    Flat bars yield RSI = 100 (avg_loss = 0). With ADX = 0 (range-bound),
    only the RSI recovery condition fires the exit.
    """
    strat = VolatilityRegimeMeanReversionStrategy()
    bars = _bars_flat("BTC/USDT", 40_000.0, 33)
    intents = strat.on_bar(_ctx("BTC/USDT", bars, position=_long("BTC/USDT")))
    assert len(intents) == 1
    assert intents[0].side == Side.SELL
    assert intents[0].symbol == "BTC/USDT"


def test_exit_on_regime_flip():
    """Has position + ADX rises above threshold → defensive SELL.

    Strong downtrend: ADX >> 25 (trending), RSI = 0 (below rsi_exit=50).
    Only the regime-flip condition fires the exit.
    """
    strat = VolatilityRegimeMeanReversionStrategy()
    bars = _bars_trending("BTC/USDT", 40_000.0, pct_per_bar=-3.0, n=35)
    intents = strat.on_bar(_ctx("BTC/USDT", bars, position=_long("BTC/USDT")))
    assert len(intents) == 1
    assert intents[0].side == Side.SELL


def test_sell_intent_has_no_stop_price():
    """SELL exit intent must not carry stop_price (RequireStopLoss exempts SELL)."""
    strat = VolatilityRegimeMeanReversionStrategy()
    bars = _bars_flat("BTC/USDT", 40_000.0, 33)
    intents = strat.on_bar(_ctx("BTC/USDT", bars, position=_long("BTC/USDT")))
    assert len(intents) == 1
    assert intents[0].side == Side.SELL
    assert intents[0].stop_price is None


# ---------------------------------------------------------------------------
# Hold: no action when conditions don't warrant entry or exit
# ---------------------------------------------------------------------------

def test_hold_in_regime_rsi_below_exit():
    """Has position, in regime, RSI < rsi_exit → hold (no action).

    30 flat + 3 declining bars: ADX ≈ 19.9 < 25 (in regime), RSI = 0 < 50.
    rsi < rsi_exit → exit condition False.
    has_position → entry condition skipped.
    Result: no intent.
    """
    strat = VolatilityRegimeMeanReversionStrategy()
    bars = _flat_then_decline("BTC/USDT", 40_000.0, n_flat=30, n_decline=3)
    intents = strat.on_bar(_ctx("BTC/USDT", bars, position=_long("BTC/USDT")))
    assert intents == []


# ---------------------------------------------------------------------------
# No duplicate BUY when already in position
# ---------------------------------------------------------------------------

def test_no_buy_when_already_in_position():
    """Strategy must not emit a BUY when a long position is already open.

    Conditions are ideal for a new entry (ADX < 25, RSI < rsi_oversold),
    but the existing position must suppress any BUY intent.
    """
    strat = VolatilityRegimeMeanReversionStrategy()
    # ADX ≈ 19.9 < 25, RSI = 0 < 30 — entry would fire for a flat position.
    bars = _flat_then_decline("BTC/USDT", 40_000.0, n_flat=30, n_decline=3)
    intents = strat.on_bar(_ctx("BTC/USDT", bars, position=_long("BTC/USDT")))
    # Must produce no intents (hold) — not a second BUY.
    assert all(i.side != Side.BUY for i in intents)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registered_as_vrfmr():
    from cryptobot.strategy.registry import get_strategy
    assert get_strategy("vrfmr") is VolatilityRegimeMeanReversionStrategy
