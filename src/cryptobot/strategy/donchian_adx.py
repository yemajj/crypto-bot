"""Donchian channel breakout with ADX confirmation gate.

Extends the raw Donchian strategy with a trend-strength filter: a breakout
signal is only emitted when ADX is at or above ``adx_threshold`` (default 25).
This aims to avoid entering whipsaw trades in ranging markets where Donchian
breakouts frequently fail.

Scores in {-1.0, 0.0, 1.0}:
  +1.0  close > prior N-bar high  AND  ADX >= adx_threshold
  -1.0  close < prior N-bar low   AND  ADX >= adx_threshold
   0.0  inside channel, ADX too weak, or insufficient bars

Set ``adx_threshold: 0`` to disable the gate entirely — the strategy then
behaves identically to the raw Donchian.
"""

from __future__ import annotations

from cryptobot.strategy.base import ScoringStrategy, StrategyContext, _compute_adx
from cryptobot.strategy.registry import register_strategy


@register_strategy("donchian_adx")
class DonchianAdxStrategy(ScoringStrategy):
    """Donchian channel breakout gated by ADX trend-strength confirmation.

    Params (all optional):
        window          int    default 20    Donchian channel period
        adx_window      int    default 14    ADX smoothing period (Wilder)
        adx_threshold   float  default 25.0  Minimum ADX to allow entry;
                                             0 disables the gate entirely
    """

    bucket = "breakout"

    def signal_score(self, ctx: StrategyContext) -> float:
        window = int(ctx.params.get("window", 20))
        adx_window = int(ctx.params.get("adx_window", 14))
        adx_threshold = float(ctx.params.get("adx_threshold", 25.0))
        bars = ctx.history

        # When the ADX gate is active, need extra history for a reliable seed.
        adx_active = adx_threshold > 0.0
        min_bars = window + 1
        if adx_active:
            min_bars = max(min_bars, 2 * adx_window + 1)

        if len(bars) < min_bars:
            return 0.0

        prior = bars[-(window + 1) : -1]  # exactly ``window`` bars before current
        channel_high = max(float(b.high) for b in prior)
        channel_low = min(float(b.low) for b in prior)
        current_close = float(bars[-1].close)

        # Check Donchian signal first — bail early if inside channel.
        if current_close > channel_high:
            raw_score = 1.0
        elif current_close < channel_low:
            raw_score = -1.0
        else:
            return 0.0

        # ADX gate: only enter when the market is actually trending.
        if adx_active:
            adx = _compute_adx(bars, adx_window)
            if adx < adx_threshold:
                return 0.0

        return raw_score
