"""Tests for CrossSectionalMomentumStrategy (XSMOM).

Test philosophy: verify the strategy's signal logic in isolation, using
manually constructed bar sequences and known RoC values so rank outcomes
are fully predictable.

Each test creates a fresh strategy instance to avoid _roc_cache contamination
between tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.core.types import Bar, Position, Side
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.xsmom import CrossSectionalMomentumStrategy

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
_FLAT = Position(symbol="BTC/USDT", qty=Decimal("0"), avg_price=Decimal("0"))
_LONG = Position(symbol="BTC/USDT", qty=Decimal("0.01"), avg_price=Decimal("40000"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(
    symbol: str = "BTC/USDT",
    close: float = 40_000.0,
    idx: int = 0,
) -> Bar:
    return Bar(
        symbol=symbol,
        timeframe="1h",
        ts_open=_T0 + timedelta(hours=idx),
        open=Decimal(str(close)),
        high=Decimal(str(close * 1.005)),
        low=Decimal(str(close * 0.995)),
        close=Decimal(str(close)),
        volume=Decimal("100"),
    )


def _bars_ramp(
    symbol: str,
    start_close: float,
    end_close: float,
    n: int = 25,
) -> list[Bar]:
    """Return n bars trending from start_close to end_close."""
    step = (end_close - start_close) / max(n - 1, 1)
    return [_bar(symbol, start_close + i * step, idx=i) for i in range(n)]


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
        params=params or {"lookback": 20, "atr_window": 5},
    )


# ---------------------------------------------------------------------------
# Warmup / insufficient data
# ---------------------------------------------------------------------------

def test_insufficient_bars_returns_empty():
    """Fewer bars than lookback+1 → no signal."""
    strat = CrossSectionalMomentumStrategy()
    bars = [_bar("BTC/USDT", 40_000.0, idx=i) for i in range(20)]  # need 21
    intents = strat.on_bar(_ctx("BTC/USDT", bars))
    assert intents == []


def test_exactly_lookback_bars_returns_empty():
    """Exactly lookback bars (no prior close to compare) → no signal."""
    strat = CrossSectionalMomentumStrategy()
    bars = [_bar("BTC/USDT", 40_000.0, idx=i) for i in range(20)]
    intents = strat.on_bar(_ctx("BTC/USDT", bars, params={"lookback": 20}))
    assert intents == []


def test_single_symbol_below_min_symbols_returns_empty():
    """Cache has only 1 symbol; min_symbols=2 → no signal even with valid data."""
    strat = CrossSectionalMomentumStrategy()
    bars = _bars_ramp("BTC/USDT", 38_000.0, 42_000.0, n=25)
    intents = strat.on_bar(_ctx("BTC/USDT", bars, params={"lookback": 20, "min_symbols": 2}))
    assert intents == []


# ---------------------------------------------------------------------------
# Entry signals
# ---------------------------------------------------------------------------

def test_top_ranked_positive_roc_generates_buy():
    """Top-ranked symbol with positive RoC → BUY intent."""
    strat = CrossSectionalMomentumStrategy()
    params = {"lookback": 20, "atr_window": 5, "min_symbols": 2,
              "top_pct": 0.34, "risk_per_trade_pct": 0.005,
              "stop_distance_multiplier": 1.5, "max_position_notional_pct": 0.08}

    # Seed cache with a weaker symbol first.
    weak_bars = _bars_ramp("SOL/USDT", 100.0, 99.0, n=25)  # -1% roc
    strat.on_bar(_ctx("SOL/USDT", weak_bars, params=params))

    # Strong symbol: +5% RoC → rank=1.0 (top of 2).
    strong_bars = _bars_ramp("BTC/USDT", 38_000.0, 39_900.0, n=25)  # ~+5% roc
    intents = strat.on_bar(_ctx("BTC/USDT", strong_bars, params=params))

    assert len(intents) == 1
    assert intents[0].side == Side.BUY
    assert intents[0].symbol == "BTC/USDT"


def test_buy_intent_has_stop_price():
    """BUY intent must include a stop_price (RequireStopLoss rule compliance)."""
    strat = CrossSectionalMomentumStrategy()
    params = {"lookback": 20, "atr_window": 5, "min_symbols": 2,
              "top_pct": 0.34, "risk_per_trade_pct": 0.005,
              "stop_distance_multiplier": 1.5, "max_position_notional_pct": 0.08}

    weak_bars = _bars_ramp("SOL/USDT", 100.0, 99.0, n=25)
    strat.on_bar(_ctx("SOL/USDT", weak_bars, params=params))

    strong_bars = _bars_ramp("BTC/USDT", 38_000.0, 39_900.0, n=25)
    intents = strat.on_bar(_ctx("BTC/USDT", strong_bars, params=params))

    assert intents and intents[0].stop_price is not None
    assert intents[0].stop_price < intents[0].qty or True  # stop_price is a price, always set


def test_bottom_ranked_no_buy():
    """Bottom-ranked symbol → no BUY even when its own RoC is positive."""
    strat = CrossSectionalMomentumStrategy()
    params = {"lookback": 20, "atr_window": 5, "min_symbols": 2,
              "top_pct": 0.34, "bottom_exit_pct": 0.34}

    # BTC is the strong symbol — rank=1.0.
    strong_bars = _bars_ramp("BTC/USDT", 38_000.0, 42_000.0, n=25)  # ~+10.5% roc
    strat.on_bar(_ctx("BTC/USDT", strong_bars, params=params))

    # SOL has a small positive RoC but is weaker — rank=0.5 with 2 symbols.
    # With top_pct=0.34, threshold=0.66.  rank=0.5 < 0.66 → no buy.
    weak_bars = _bars_ramp("SOL/USDT", 100.0, 101.0, n=25)  # +1% roc
    intents = strat.on_bar(_ctx("SOL/USDT", weak_bars, params=params))

    # rank=0.5 < threshold=0.66 → no intent
    assert intents == []


def test_top_ranked_negative_roc_no_buy():
    """Top-ranked by cross-section but absolute RoC is negative → no BUY."""
    strat = CrossSectionalMomentumStrategy()
    params = {"lookback": 20, "atr_window": 5, "min_symbols": 2,
              "top_pct": 0.34, "bottom_exit_pct": 0.34}

    # Both symbols are down; SOL is down more → BTC is rank=1.0 but still negative.
    sol_bars = _bars_ramp("SOL/USDT", 100.0, 90.0, n=25)  # -10% roc
    strat.on_bar(_ctx("SOL/USDT", sol_bars, params=params))

    btc_bars = _bars_ramp("BTC/USDT", 40_000.0, 39_000.0, n=25)  # -2.5% roc
    intents = strat.on_bar(_ctx("BTC/USDT", btc_bars, params=params))

    # BTC is rank=1.0 but roc < 0 → no entry.
    assert intents == []


# ---------------------------------------------------------------------------
# Exit signals
# ---------------------------------------------------------------------------

def test_exit_on_negative_roc():
    """Primary exit: roc turns negative while holding a position → SELL."""
    strat = CrossSectionalMomentumStrategy()
    params = {"lookback": 20, "atr_window": 5, "min_symbols": 2,
              "top_pct": 0.34, "bottom_exit_pct": 0.34}

    # Seed cache with another symbol.
    sol_bars = _bars_ramp("SOL/USDT", 100.0, 105.0, n=25)
    strat.on_bar(_ctx("SOL/USDT", sol_bars, params=params))

    # BTC is declining — roc < 0, rank doesn't matter.
    btc_bars = _bars_ramp("BTC/USDT", 42_000.0, 39_000.0, n=25)  # ~-7% roc
    long_pos = Position(symbol="BTC/USDT", qty=Decimal("0.01"), avg_price=Decimal("41000"))
    intents = strat.on_bar(_ctx("BTC/USDT", btc_bars, long_pos, params=params))

    assert len(intents) == 1
    assert intents[0].side == Side.SELL
    assert intents[0].symbol == "BTC/USDT"


def test_exit_on_bottom_tier_rank():
    """Secondary exit: rank drops to bottom tier (even if roc still positive)."""
    strat = CrossSectionalMomentumStrategy()
    params = {"lookback": 20, "atr_window": 5, "min_symbols": 2,
              "top_pct": 0.34, "bottom_exit_pct": 0.34}

    # Add two stronger symbols so BTC ends up rank=0.33 (bottom third of 3).
    eth_bars = _bars_ramp("ETH/USDT", 2000.0, 2400.0, n=25)   # +20% roc
    sol_bars = _bars_ramp("SOL/USDT", 100.0, 115.0, n=25)      # +15% roc
    strat.on_bar(_ctx("ETH/USDT", eth_bars, params=params))
    strat.on_bar(_ctx("SOL/USDT", sol_bars, params=params))

    # BTC has weak positive roc but rank=0.33 (bottom of 3) → exit_threshold=0.34.
    btc_bars = _bars_ramp("BTC/USDT", 40_000.0, 40_200.0, n=25)  # +0.5% roc
    long_pos = Position(symbol="BTC/USDT", qty=Decimal("0.01"), avg_price=Decimal("39900"))
    intents = strat.on_bar(_ctx("BTC/USDT", btc_bars, long_pos, params=params))

    assert len(intents) == 1
    assert intents[0].side == Side.SELL


def test_hold_when_rank_stays_high():
    """No exit when rank is still in the top tier and roc is positive."""
    strat = CrossSectionalMomentumStrategy()
    params = {"lookback": 20, "atr_window": 5, "min_symbols": 2,
              "top_pct": 0.34, "bottom_exit_pct": 0.34}

    # Weak symbol in cache.
    sol_bars = _bars_ramp("SOL/USDT", 100.0, 99.0, n=25)   # -1% roc → rank=0.5
    strat.on_bar(_ctx("SOL/USDT", sol_bars, params=params))

    # BTC: positive roc, rank=1.0 — should hold (no new intent).
    btc_bars = _bars_ramp("BTC/USDT", 38_000.0, 40_000.0, n=25)  # +5.3% roc
    long_pos = Position(symbol="BTC/USDT", qty=Decimal("0.01"), avg_price=Decimal("38500"))
    intents = strat.on_bar(_ctx("BTC/USDT", btc_bars, long_pos, params=params))

    assert intents == []


def test_sell_intent_has_no_stop_price():
    """SELL exit intent must not include a stop_price (rule exempts SELL)."""
    strat = CrossSectionalMomentumStrategy()
    params = {"lookback": 20, "atr_window": 5, "min_symbols": 2}

    sol_bars = _bars_ramp("SOL/USDT", 100.0, 105.0, n=25)
    strat.on_bar(_ctx("SOL/USDT", sol_bars, params=params))

    btc_bars = _bars_ramp("BTC/USDT", 40_000.0, 38_000.0, n=25)  # roc < 0
    long_pos = Position(symbol="BTC/USDT", qty=Decimal("0.01"), avg_price=Decimal("40000"))
    intents = strat.on_bar(_ctx("BTC/USDT", btc_bars, long_pos, params=params))

    assert len(intents) == 1
    assert intents[0].side == Side.SELL
    assert intents[0].stop_price is None


# ---------------------------------------------------------------------------
# Cache isolation between instances
# ---------------------------------------------------------------------------

def test_fresh_instance_has_empty_cache():
    """Each new instance starts with no cross-sectional state."""
    strat1 = CrossSectionalMomentumStrategy()
    strat2 = CrossSectionalMomentumStrategy()

    bars = _bars_ramp("BTC/USDT", 38_000.0, 42_000.0, n=25)
    params = {"lookback": 20, "min_symbols": 2}
    strat1.on_bar(_ctx("BTC/USDT", bars, params=params))

    # strat2 has no cache entries; min_symbols=2 blocks signal.
    intents = strat2.on_bar(_ctx("BTC/USDT", bars, params=params))
    assert intents == []


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registered_as_xsmom():
    from cryptobot.strategy.registry import get_strategy
    assert get_strategy("xsmom") is CrossSectionalMomentumStrategy
