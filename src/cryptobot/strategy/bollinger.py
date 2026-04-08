"""Bollinger Band expansion-confirmation strategy.

v1 behavior: silent during compression, active on bandwidth expansion.
  - Expanding bandwidth + close above midline → positive score
  - Expanding bandwidth + close below midline → negative score
  - Contracting or flat bandwidth             → 0.0

Score is the position of close within the upper (or lower) half of the bands,
normalized to [-1.0, 1.0] and clamped.

Designed for future upgrade to full squeeze-detection (compression phase
signals anticipation of breakout) without changing the public interface.
"""

from __future__ import annotations

import math

from cryptobot.strategy.base import ScoringStrategy, StrategyContext
from cryptobot.strategy.registry import register_strategy


def _bands(closes: list[float], n_std: float) -> tuple[float, float, float, float]:
    """Return (middle, upper, lower, bandwidth) for the given closes.

    bandwidth = (upper - lower) / middle.
    Returns (0, 0, 0, 0) if std == 0 or fewer than 2 values.
    """
    if len(closes) < 2:
        return 0.0, 0.0, 0.0, 0.0
    n = len(closes)
    mean = sum(closes) / n
    variance = sum((c - mean) ** 2 for c in closes) / n
    std = math.sqrt(variance)
    if std == 0.0:
        return mean, mean, mean, 0.0
    upper = mean + n_std * std
    lower = mean - n_std * std
    bw = (upper - lower) / mean if mean != 0.0 else 0.0
    return mean, upper, lower, bw


@register_strategy("bollinger")
class BollingerStrategy(ScoringStrategy):
    """Bollinger Band expansion-confirmation strategy.

    Params (all optional):
        window  int    default 20
        n_std   float  default 2.0
    """

    bucket = "volatility"

    def signal_score(self, ctx: StrategyContext) -> float:
        window = int(ctx.params.get("window", 20))
        n_std = float(ctx.params.get("n_std", 2.0))
        bars = ctx.history

        # Need current window + one prior window bar for bandwidth comparison.
        if len(bars) < window + 1:
            return 0.0

        closes_now = [float(b.close) for b in bars[-window:]]
        closes_prev = [float(b.close) for b in bars[-(window + 1):-1]]

        middle, upper, lower, bw_now = _bands(closes_now, n_std)
        _, _, _, bw_prev = _bands(closes_prev, n_std)

        # Guard: degenerate bands
        if bw_now == 0.0 or upper == middle:
            return 0.0

        # Silent during compression or flat bandwidth.
        if bw_now <= bw_prev:
            return 0.0

        # Expanding: score = position of close within the half-band.
        current_close = float(bars[-1].close)
        raw = (current_close - middle) / (upper - middle)
        return max(-1.0, min(1.0, raw))
