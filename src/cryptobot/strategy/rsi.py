"""RSI momentum strategy.

Scores in [-1.0, 1.0]:
  - RSI ≤ oversold threshold → positive score (bullish)
  - RSI ≥ overbought threshold → negative score (bearish)
  - Neutral zone → 0.0

Uses Wilder's RSI with equal-weight seed for the first window of gains/losses.
Returns 0.0 (neutral) when there are insufficient bars.
"""

from __future__ import annotations

from cryptobot.strategy.base import ScoringStrategy, StrategyContext
from cryptobot.strategy.registry import register_strategy


def _rsi(closes: list[float], window: int) -> float:
    """Compute RSI for the last bar.

    Requires at least ``window + 1`` values.  Returns 50.0 (neutral) if
    there are insufficient data points.

    Uses equal-weight average gain/loss seed (Wilder's method, simplified
    for a fixed lookback window — acceptable for period-14 in v1).
    """
    if len(closes) < window + 1:
        return 50.0

    deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - window, len(closes))]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]

    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


@register_strategy("rsi")
class RsiStrategy(ScoringStrategy):
    """RSI mean-reversion strategy.

    Params (all optional):
        window     int    default 14
        oversold   float  default 30.0
        overbought float  default 70.0
    """

    bucket = "momentum"

    def signal_score(self, ctx: StrategyContext) -> float:
        window = int(ctx.params.get("window", 14))
        oversold = float(ctx.params.get("oversold", 30.0))
        overbought = float(ctx.params.get("overbought", 70.0))

        closes = [float(b.close) for b in ctx.history]
        rsi = _rsi(closes, window)

        if rsi <= oversold:
            # Approaches 1.0 as RSI → 0
            return (oversold - rsi) / oversold
        if rsi >= overbought:
            # Approaches -1.0 as RSI → 100
            return -(rsi - overbought) / (100.0 - overbought)
        return 0.0
