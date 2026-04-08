"""Volume confidence scorer.

Scores in [-1.0, 1.0] based on whether current volume confirms price direction:
  - Above-average volume + price up   → positive score
  - Above-average volume + price down → negative score
  - Below-average (or flat) volume    → 0.0

Used by EnsembleStrategy as a confidence multiplier, not a directional bucket.
Can also be used standalone via the default ScoringStrategy.on_bar().
"""

from __future__ import annotations

from cryptobot.strategy.base import ScoringStrategy, StrategyContext
from cryptobot.strategy.registry import register_strategy


@register_strategy("volume_signal")
class VolumeSignalStrategy(ScoringStrategy):
    """Volume confirmation scorer.

    Params (all optional):
        window  int  default 20
    """

    bucket = "volume"

    def signal_score(self, ctx: StrategyContext) -> float:
        window = int(ctx.params.get("window", 20))
        bars = ctx.history

        # Need current bar + window prior bars + one bar before them for prev_close.
        if len(bars) < window + 2:
            return 0.0

        prior_vols = [float(b.volume) for b in bars[-(window + 1):-1]]
        avg_vol = sum(prior_vols) / len(prior_vols)
        if avg_vol <= 0.0:
            return 0.0

        current_vol = float(bars[-1].volume)
        vol_ratio = current_vol / avg_vol

        if vol_ratio <= 1.0:
            return 0.0   # below-average volume — no confirmation

        # vol_factor: 0 at 1× average, 1.0 at 3× average
        vol_factor = min(1.0, (vol_ratio - 1.0) / 2.0)

        current_close = float(bars[-1].close)
        prev_close = float(bars[-2].close)
        price_direction = 1 if current_close > prev_close else -1

        return price_direction * vol_factor
