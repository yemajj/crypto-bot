"""Strategy base class, context, and ScoringStrategy ABC.

Strategies are **pure**: given a context (current bar + recent history +
current position), they return a list of Intents. Side effects (orders,
logging of fills, etc.) happen in the execution layer, never here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from cryptobot.core.types import Bar, Intent, OrderType, Position, Side


@dataclass
class StrategyContext:
    """What a strategy sees on each step.

    `history` is the recent bars for the symbol, oldest-first, ending with
    the most recently closed bar. `position` is the current net position for
    the symbol (may be flat).
    """

    symbol: str
    history: list[Bar]
    position: Position
    equity: float   # portfolio equity in quote currency
    params: dict[str, Any]


class Strategy(ABC):
    """Base class for all strategies."""

    #: Stable identifier used in logs and the journal.
    name: str = "base"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = params or {}

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        """Return zero or more Intents for this bar. Must be pure."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Shared ATR helper — used by ScoringStrategy subclasses and regime_detector.
# sma_crossover.py keeps its own _atr() so its existing tests are unaffected.
# ---------------------------------------------------------------------------

def _compute_atr(bars: list[Bar], window: int) -> float:
    """Average True Range over the last `window` bars.

    Returns 0.0 if there are fewer than ``window + 1`` bars.
    """
    if len(bars) < window + 1:
        return 0.0
    trs: list[float] = []
    for i in range(-window, 0):
        b = bars[i]
        prev_close = float(bars[i - 1].close)
        high = float(b.high)
        low = float(b.low)
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs) / len(trs)


def _compute_adx(bars: list[Bar], window: int = 14) -> float:
    """Average Directional Index (Wilder smoothing).

    Measures trend *strength* regardless of direction.
    Returns a value in [0, 100]; values >= 25 indicate a meaningful trend.
    Returns 0.0 when bars are insufficient (needs 2*window + 1 bars for a
    reliable seed).
    """
    needed = 2 * window + 1
    if len(bars) < needed:
        return 0.0

    # Seed Wilder smoothing using a simple average of the first `window` bars.
    seed_bars = bars[: window + 1]
    tr_sum = plus_dm_sum = minus_dm_sum = 0.0
    for i in range(1, len(seed_bars)):
        b, prev = seed_bars[i], seed_bars[i - 1]
        high, low = float(b.high), float(b.low)
        prev_high, prev_low, prev_close = float(prev.high), float(prev.low), float(prev.close)
        tr_sum += max(high - low, abs(high - prev_close), abs(low - prev_close))
        up, dn = high - prev_high, prev_low - low
        plus_dm_sum += up if up > dn and up > 0 else 0.0
        minus_dm_sum += dn if dn > up and dn > 0 else 0.0

    atr_w = tr_sum / window
    plus_di_w = plus_dm_sum / window
    minus_di_w = minus_dm_sum / window

    # Accumulate DX values after the seed window.
    dx_values: list[float] = []
    for i in range(window + 1, len(bars)):
        b, prev = bars[i], bars[i - 1]
        high, low = float(b.high), float(b.low)
        prev_high, prev_low, prev_close = float(prev.high), float(prev.low), float(prev.close)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up, dn = high - prev_high, prev_low - low
        plus_dm = up if up > dn and up > 0 else 0.0
        minus_dm = dn if dn > up and dn > 0 else 0.0

        # Wilder smoothing: new = prev * (n-1)/n + current
        atr_w = atr_w * (window - 1) / window + tr
        plus_di_w = plus_di_w * (window - 1) / window + plus_dm
        minus_di_w = minus_di_w * (window - 1) / window + minus_dm

        plus_di = 100.0 * plus_di_w / atr_w if atr_w > 0 else 0.0
        minus_di = 100.0 * minus_di_w / atr_w if atr_w > 0 else 0.0
        di_sum = plus_di + minus_di
        dx_values.append(100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0)

    if not dx_values:
        return 0.0

    # ADX = Wilder-smoothed DX; seed with simple mean of the first `window` DX values.
    seed_dx = dx_values[:window]
    adx = sum(seed_dx) / len(seed_dx)
    for dx in dx_values[window:]:
        adx = adx * (window - 1) / window + dx / window
    return adx


class ScoringStrategy(Strategy):
    """Strategy that exposes a scalar score in [-1.0, 1.0] via signal_score().

    Subclasses must implement signal_score(). The default on_bar() converts
    the score to a BUY or SELL Intent using configurable thresholds.

    Class attribute:
        bucket — one of "trend", "momentum", "breakout", "volatility",
                 "volume". Used by EnsembleStrategy to route scores.
    """

    bucket: str = "unknown"

    @abstractmethod
    def signal_score(self, ctx: StrategyContext) -> float:
        """Return a score in [-1.0, 1.0]. Pure — no side effects.

        Positive values indicate bullish bias; negative indicate bearish bias.
        Return 0.0 when there is no signal or insufficient data.
        """
        ...

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        """Convert signal_score to an Intent using threshold params.

        Reads from ctx.params (all optional):
            buy_threshold            float  default 0.30
            sell_threshold           float  default -0.30
            atr_window               int    default 14
            risk_per_trade_pct       float  default 0.01
            stop_distance_multiplier float  default 1.5
        """
        score = self.signal_score(ctx)

        buy_threshold = float(ctx.params.get("buy_threshold", 0.30))
        sell_threshold = float(ctx.params.get("sell_threshold", -0.30))

        if score >= buy_threshold and ctx.position.qty <= 0:
            atr_window = int(ctx.params.get("atr_window", 14))
            risk_pct = float(ctx.params.get("risk_per_trade_pct", 0.01))
            multiplier = float(ctx.params.get("stop_distance_multiplier", 1.5))

            atr = _compute_atr(ctx.history, atr_window)
            if atr == 0.0:
                return []

            stop_distance = atr * multiplier
            current_close = float(ctx.history[-1].close)
            risk_amount = ctx.equity * risk_pct
            qty = Decimal(str(round(risk_amount / stop_distance, 8)))
            stop_price = Decimal(str(round(current_close - stop_distance, 8)))

            return [Intent(
                strategy_id=self.name,
                symbol=ctx.symbol,
                side=Side.BUY,
                qty=qty,
                order_type=OrderType.MARKET,
                stop_price=stop_price,
                reason=f"{self.name} score={score:.3f}",
            )]

        if score <= sell_threshold and ctx.position.qty > 0:
            return [Intent(
                strategy_id=self.name,
                symbol=ctx.symbol,
                side=Side.SELL,
                qty=ctx.position.qty,
                order_type=OrderType.MARKET,
                reason=f"{self.name} score={score:.3f}",
            )]

        return []
