"""SMA crossover starter strategy (skeleton).

The real signal logic lands in Phase 3. For Phase 1 we only register the
class so the registry wiring is exercised by tests.
"""

from __future__ import annotations

from cryptobot.core.types import Intent
from cryptobot.strategy.base import Strategy, StrategyContext
from cryptobot.strategy.registry import register_strategy


@register_strategy("sma_crossover")
class SmaCrossover(Strategy):
    """Single-asset SMA crossover with volatility-scaled sizing.

    Params (Phase 3):
        fast: int — fast SMA window
        slow: int — slow SMA window
        risk_per_trade_pct: float — fraction of equity risked per trade
        atr_window: int — ATR window for position sizing / stops
    """

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        # TODO (Phase 3): compute fast/slow SMAs over ctx.history, detect
        # crossover events, size position by ATR and equity, always attach a
        # stop-loss. Must remain a pure function of ctx.
        return []
