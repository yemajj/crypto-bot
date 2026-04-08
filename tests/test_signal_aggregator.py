"""Tests for SignalAggregator."""

from __future__ import annotations

import pytest

from cryptobot.strategy.regime_detector import Regime
from cryptobot.strategy.signal_aggregator import (
    DEFAULT_REGIME_WEIGHTS,
    AggregationResult,
    SignalAggregator,
)

_TRENDING = Regime.TRENDING
_RANGING = Regime.RANGING
_BREAKOUT = Regime.BREAKOUT_WATCH


def _agg(**kwargs) -> SignalAggregator:
    defaults = dict(
        buy_threshold=0.30,
        sell_threshold=-0.30,
        min_agreeing_buckets=2,
        agreement_min_magnitude=0.25,
        volume_multiplier_scale=0.25,
    )
    defaults.update(kwargs)
    return SignalAggregator(**defaults)


# ---------------------------------------------------------------------------
# Basic aggregation
# ---------------------------------------------------------------------------

def test_all_positive_scores_agree_and_pass_threshold():
    agg = _agg()
    scores = {"trend": 0.8, "momentum": 0.7, "breakout": 0.6, "volatility": 0.5}
    result = agg.aggregate(scores, _TRENDING)
    assert result.final_score > 0.30
    assert result.agreement_ok is True


def test_all_negative_scores_agree_and_pass_threshold():
    agg = _agg()
    scores = {"trend": -0.8, "momentum": -0.7, "breakout": -0.6, "volatility": -0.5}
    result = agg.aggregate(scores, _TRENDING)
    assert result.final_score < -0.30
    assert result.agreement_ok is True


def test_zero_scores_no_agreement():
    agg = _agg()
    scores = {"trend": 0.0, "momentum": 0.0, "breakout": 0.0, "volatility": 0.0}
    result = agg.aggregate(scores, _TRENDING)
    assert result.final_score == pytest.approx(0.0)
    assert result.agreement_ok is False


def test_one_agreeing_bucket_fails_filter():
    """Only 1 bucket with meaningful magnitude — fails min_agreeing_buckets=2."""
    agg = _agg()
    scores = {"trend": 0.8, "momentum": 0.1, "breakout": 0.0, "volatility": 0.0}
    result = agg.aggregate(scores, _TRENDING)
    # trend agrees (0.8 >= 0.25), others don't qualify
    assert result.agreement_ok is False


def test_two_agreeing_buckets_passes_filter():
    agg = _agg()
    scores = {"trend": 0.8, "momentum": 0.6, "breakout": 0.0, "volatility": 0.0}
    result = agg.aggregate(scores, _TRENDING)
    assert result.agreement_ok is True


def test_low_magnitude_bucket_not_counted():
    """Score of 0.1 is below agreement_min_magnitude=0.25 → not counted."""
    agg = _agg(agreement_min_magnitude=0.25)
    scores = {"trend": 0.8, "momentum": 0.1, "breakout": 0.26, "volatility": 0.0}
    result = agg.aggregate(scores, _TRENDING)
    # trend (0.8) and breakout (0.26) both agree → 2 → passes
    assert result.agreement_ok is True


def test_opposing_bucket_not_counted_as_agreeing():
    agg = _agg()
    scores = {"trend": 0.8, "momentum": -0.8, "breakout": 0.0, "volatility": 0.0}
    result = agg.aggregate(scores, _TRENDING)
    # final_score likely slightly positive; only trend agrees
    assert result.agreement_ok is False


# ---------------------------------------------------------------------------
# Weight re-normalization for missing buckets
# ---------------------------------------------------------------------------

def test_missing_bucket_excluded_not_zero_weighted():
    """With only 2 buckets present, their weights re-normalize to sum to 1.0."""
    agg = _agg()
    # Only trend and momentum present
    scores = {"trend": 1.0, "momentum": 1.0}
    result = agg.aggregate(scores, _TRENDING)
    # Trending weights: trend=0.40, momentum=0.20 → norm: 0.667, 0.333
    expected_raw = (1.0 * 0.40 + 1.0 * 0.20) / (0.40 + 0.20)
    assert result.raw_score == pytest.approx(expected_raw, abs=1e-6)


def test_single_bucket_gives_full_weight():
    agg = _agg()
    scores = {"breakout": 0.5}
    result = agg.aggregate(scores, _BREAKOUT)
    assert result.raw_score == pytest.approx(0.5)


def test_no_buckets_returns_zero_no_agreement():
    agg = _agg()
    result = agg.aggregate({}, _TRENDING)
    assert result.final_score == 0.0
    assert result.agreement_ok is False


# ---------------------------------------------------------------------------
# Volume multiplier
# ---------------------------------------------------------------------------

def test_positive_volume_boosts_positive_score():
    agg = _agg(volume_multiplier_scale=0.25)
    scores = {"trend": 0.8, "momentum": 0.8, "breakout": 0.8, "volatility": 0.8}
    result_no_vol = agg.aggregate(scores, _TRENDING, volume_score=0.0)
    result_with_vol = agg.aggregate(scores, _TRENDING, volume_score=1.0)
    assert result_with_vol.final_score >= result_no_vol.final_score


def test_negative_volume_dampens_positive_score():
    agg = _agg(volume_multiplier_scale=0.25)
    scores = {"trend": 0.6, "momentum": 0.6, "breakout": 0.6, "volatility": 0.6}
    result_no_vol = agg.aggregate(scores, _TRENDING, volume_score=0.0)
    result_dampened = agg.aggregate(scores, _TRENDING, volume_score=-1.0)
    assert result_dampened.final_score < result_no_vol.final_score


def test_final_score_clamped_to_minus_one_one():
    agg = _agg(volume_multiplier_scale=1.0)
    scores = {"trend": 1.0, "momentum": 1.0, "breakout": 1.0, "volatility": 1.0}
    result = agg.aggregate(scores, _TRENDING, volume_score=1.0)
    assert result.final_score <= 1.0


# ---------------------------------------------------------------------------
# Regime weights applied correctly
# ---------------------------------------------------------------------------

def test_regime_weights_differ_by_regime():
    agg = _agg()
    scores = {"trend": 1.0, "momentum": 0.0, "breakout": 0.0, "volatility": 0.0}
    # In TRENDING, trend has weight 0.40; in RANGING it has 0.12
    r_trending = agg.aggregate(scores, _TRENDING)
    r_ranging = agg.aggregate(scores, _RANGING)
    # Trend-only score should be higher in TRENDING than RANGING
    assert r_trending.raw_score > r_ranging.raw_score


def test_result_contains_bucket_scores_for_logging():
    agg = _agg()
    scores = {"trend": 0.5, "momentum": 0.3}
    result = agg.aggregate(scores, _TRENDING)
    assert result.bucket_scores == scores
    assert result.volume_score == 0.0
    assert result.regime == _TRENDING
