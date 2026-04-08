"""Weighted signal aggregator for the ensemble strategy.

Combines scores from up to 4 directional buckets (trend, momentum, breakout,
volatility) using regime-aware weights, then applies a volume confidence
multiplier.  Weights are re-normalized at runtime across only the buckets that
are actually present, so a missing bucket does not pull the score toward zero.

Agreement filter: a bucket "agrees" when
  abs(score) >= agreement_min_magnitude  AND  sign(score) == sign(final_score)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cryptobot.strategy.regime_detector import Regime

# Default regime → bucket weights (4 directional buckets only; volume is separate).
# Each row sums to 1.0 after dropping the volume column from the original 5-bucket design.
DEFAULT_REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "trending": {
        "trend": 0.40,
        "momentum": 0.20,
        "breakout": 0.25,
        "volatility": 0.15,
    },
    "ranging": {
        "trend": 0.12,
        "momentum": 0.40,
        "breakout": 0.13,
        "volatility": 0.35,
    },
    "breakout_watch": {
        "trend": 0.18,
        "momentum": 0.12,
        "breakout": 0.40,
        "volatility": 0.30,
    },
}

# Maximum fractional adjustment from volume confirmation (±25%).
_DEFAULT_VOLUME_SCALE: float = 0.25


@dataclass(frozen=True)
class AggregationResult:
    """Result of a single aggregation call."""

    final_score: float              # after volume multiplier, clamped [-1.0, 1.0]
    raw_score: float                # before volume multiplier
    regime: Regime
    agreement_ok: bool
    bucket_scores: dict[str, float] = field(default_factory=dict)   # for logging
    volume_score: float = 0.0                                        # for logging


class SignalAggregator:
    """Aggregate per-bucket scores into a single trading signal."""

    def __init__(
        self,
        regime_weights: dict[str, dict[str, float]] | None = None,
        buy_threshold: float = 0.30,
        sell_threshold: float = -0.30,
        min_agreeing_buckets: int = 2,
        agreement_min_magnitude: float = 0.25,
        volume_multiplier_scale: float = _DEFAULT_VOLUME_SCALE,
    ) -> None:
        self._weights = regime_weights if regime_weights is not None else DEFAULT_REGIME_WEIGHTS
        self._buy_threshold = buy_threshold
        self._sell_threshold = sell_threshold
        self._min_agreeing = min_agreeing_buckets
        self._agreement_mag = agreement_min_magnitude
        self._vol_scale = volume_multiplier_scale

    def aggregate(
        self,
        bucket_scores: dict[str, float],
        regime: Regime,
        volume_score: float = 0.0,
    ) -> AggregationResult:
        """Compute the final score for this bar.

        Args:
            bucket_scores: Mapping of bucket name → score in [-1.0, 1.0].
                           Only buckets present here are included; others are
                           excluded and their weights redistributed.
            regime:        Current market regime.
            volume_score:  Score from VolumeSignalStrategy (default 0.0 = neutral).

        Returns:
            AggregationResult with final_score, raw_score, regime,
            agreement_ok, bucket_scores, and volume_score.
        """
        regime_key = regime.value
        base_weights = self._weights.get(regime_key, DEFAULT_REGIME_WEIGHTS["ranging"])

        # Re-normalize weights across only the present buckets.
        active: dict[str, float] = {
            b: w for b, w in base_weights.items() if b in bucket_scores
        }
        total_w = sum(active.values())

        if total_w == 0.0:
            return AggregationResult(
                final_score=0.0,
                raw_score=0.0,
                regime=regime,
                agreement_ok=False,
                bucket_scores=dict(bucket_scores),
                volume_score=volume_score,
            )

        norm_weights = {b: w / total_w for b, w in active.items()}
        raw_score = sum(bucket_scores[b] * norm_weights[b] for b in active)
        raw_score = max(-1.0, min(1.0, raw_score))

        # Volume multiplier: ±volume_multiplier_scale × vol_score adjustment.
        multiplier = 1.0 + self._vol_scale * volume_score
        final_score = max(-1.0, min(1.0, raw_score * multiplier))

        # Agreement filter.
        agreeing = sum(
            1
            for b, score in bucket_scores.items()
            if b in active
            and abs(score) >= self._agreement_mag
            and (score > 0) == (final_score > 0)
        )
        agreement_ok = agreeing >= self._min_agreeing

        return AggregationResult(
            final_score=final_score,
            raw_score=raw_score,
            regime=regime,
            agreement_ok=agreement_ok,
            bucket_scores=dict(bucket_scores),
            volume_score=volume_score,
        )
