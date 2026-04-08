"""Donchian channel breakout strategy.

Scores in {-1.0, 0.0, 1.0}:
  - Close > prior N-bar high  → +1.0 (bullish breakout)
  - Close < prior N-bar low   → -1.0 (bearish breakdown)
  - Inside channel            →  0.0

The channel is computed from the prior ``window`` bars, excluding the current
bar, to avoid look-ahead bias.  Returns 0.0 when there are insufficient bars.
"""

from __future__ import annotations

from cryptobot.strategy.base import ScoringStrategy, StrategyContext
from cryptobot.strategy.registry import register_strategy


@register_strategy("donchian")
class DonchianStrategy(ScoringStrategy):
    """Donchian channel breakout strategy.

    Params (all optional):
        window  int  default 20
    """

    bucket = "breakout"

    def signal_score(self, ctx: StrategyContext) -> float:
        window = int(ctx.params.get("window", 20))
        bars = ctx.history

        # Need the current bar plus ``window`` prior bars.
        if len(bars) < window + 1:
            return 0.0

        prior = bars[-(window + 1):-1]   # exactly ``window`` bars before current
        channel_high = max(float(b.high) for b in prior)
        channel_low = min(float(b.low) for b in prior)
        current_close = float(bars[-1].close)

        if current_close > channel_high:
            return 1.0
        if current_close < channel_low:
            return -1.0
        return 0.0
