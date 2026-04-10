"""Tests for allocation/allocator.py — FixedRiskAllocator and RegimeScaledAllocator."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from cryptobot.allocation.allocator import (
    FixedRiskAllocator,
    RegimeScaledAllocator,
)
from cryptobot.core.regimes import Regime


# ---------------------------------------------------------------------------
# FixedRiskAllocator
# ---------------------------------------------------------------------------

class TestFixedRiskAllocator:
    def test_basic_calculation(self):
        """qty = (equity × risk_pct) / (atr × stop_mult)"""
        alloc = FixedRiskAllocator(risk_per_trade_pct=0.01, stop_distance_multiplier=2.0)
        # equity=10000, risk=1%, risk_amount=100, atr=100, stop=200 → qty=0.5
        qty = alloc.allocate(score=1.0, regime=Regime.TRENDING, equity=10_000.0, atr=100.0)
        assert qty == Decimal("0.5")

    def test_returns_decimal(self):
        alloc = FixedRiskAllocator()
        qty = alloc.allocate(1.0, Regime.RANGING, 10_000.0, 50.0)
        assert isinstance(qty, Decimal)

    def test_zero_atr_returns_zero(self):
        alloc = FixedRiskAllocator()
        qty = alloc.allocate(1.0, Regime.TRENDING, 10_000.0, atr=0.0)
        assert qty == Decimal("0")

    def test_negative_atr_returns_zero(self):
        alloc = FixedRiskAllocator()
        qty = alloc.allocate(1.0, Regime.TRENDING, 10_000.0, atr=-1.0)
        assert qty == Decimal("0")

    def test_zero_equity_returns_zero(self):
        alloc = FixedRiskAllocator()
        qty = alloc.allocate(1.0, Regime.TRENDING, equity=0.0, atr=50.0)
        assert qty == Decimal("0")

    def test_negative_equity_returns_zero(self):
        alloc = FixedRiskAllocator()
        qty = alloc.allocate(1.0, Regime.TRENDING, equity=-100.0, atr=50.0)
        assert qty == Decimal("0")

    def test_score_does_not_affect_qty(self):
        """FixedRiskAllocator is score-agnostic; score is for future Kelly-style use."""
        alloc = FixedRiskAllocator(risk_per_trade_pct=0.005, stop_distance_multiplier=1.5)
        qty_low = alloc.allocate(0.1, Regime.RANGING, 10_000.0, 50.0)
        qty_high = alloc.allocate(0.9, Regime.RANGING, 10_000.0, 50.0)
        assert qty_low == qty_high

    def test_regime_does_not_affect_base_qty(self):
        """FixedRiskAllocator is regime-agnostic; regime scaling is RegimeScaledAllocator's job."""
        alloc = FixedRiskAllocator()
        qty_trend = alloc.allocate(1.0, Regime.TRENDING, 10_000.0, 50.0)
        qty_range = alloc.allocate(1.0, Regime.RANGING, 10_000.0, 50.0)
        qty_break = alloc.allocate(1.0, Regime.BREAKOUT_WATCH, 10_000.0, 50.0)
        assert qty_trend == qty_range == qty_break

    def test_qty_proportional_to_equity(self):
        alloc = FixedRiskAllocator(risk_per_trade_pct=0.01, stop_distance_multiplier=1.0)
        qty_small = alloc.allocate(1.0, Regime.TRENDING, 5_000.0, 100.0)
        qty_large = alloc.allocate(1.0, Regime.TRENDING, 10_000.0, 100.0)
        assert float(qty_large) == pytest.approx(float(qty_small) * 2, rel=1e-6)

    def test_qty_inversely_proportional_to_atr(self):
        alloc = FixedRiskAllocator(risk_per_trade_pct=0.01, stop_distance_multiplier=1.0)
        qty_low_atr = alloc.allocate(1.0, Regime.TRENDING, 10_000.0, 50.0)
        qty_high_atr = alloc.allocate(1.0, Regime.TRENDING, 10_000.0, 100.0)
        assert float(qty_low_atr) == pytest.approx(float(qty_high_atr) * 2, rel=1e-6)

    def test_result_rounded_to_8_decimal_places(self):
        alloc = FixedRiskAllocator(risk_per_trade_pct=0.01, stop_distance_multiplier=3.0)
        qty = alloc.allocate(1.0, Regime.TRENDING, 10_000.0, 33.0)
        # Verify at most 8 decimal places
        s = str(qty)
        if "." in s:
            assert len(s.split(".")[1]) <= 8

    def test_default_params(self):
        """Defaults: risk_per_trade_pct=0.005, stop_distance_multiplier=1.5"""
        alloc = FixedRiskAllocator()
        # equity=10000, risk=0.5%, risk_amount=50, atr=100, stop=150 → qty=50/150≈0.33333333
        qty = alloc.allocate(1.0, Regime.TRENDING, 10_000.0, 100.0)
        expected = round(50.0 / 150.0, 8)
        assert float(qty) == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# RegimeScaledAllocator
# ---------------------------------------------------------------------------

class TestRegimeScaledAllocator:
    def _base(self) -> FixedRiskAllocator:
        # Produces qty = 1.0 for equity=10000, atr=50, risk=0.5%, stop_mult=1.0
        return FixedRiskAllocator(risk_per_trade_pct=0.005, stop_distance_multiplier=1.0)

    def test_trending_factor_is_1x_by_default(self):
        alloc = RegimeScaledAllocator(self._base())
        base_qty = self._base().allocate(1.0, Regime.TRENDING, 10_000.0, 50.0)
        scaled_qty = alloc.allocate(1.0, Regime.TRENDING, 10_000.0, 50.0)
        assert float(scaled_qty) == pytest.approx(float(base_qty) * 1.0, rel=1e-6)

    def test_ranging_factor_is_0_5x_by_default(self):
        alloc = RegimeScaledAllocator(self._base())
        base_qty = self._base().allocate(1.0, Regime.RANGING, 10_000.0, 50.0)
        scaled_qty = alloc.allocate(1.0, Regime.RANGING, 10_000.0, 50.0)
        assert float(scaled_qty) == pytest.approx(float(base_qty) * 0.5, rel=1e-6)

    def test_breakout_factor_is_0_75x_by_default(self):
        alloc = RegimeScaledAllocator(self._base())
        base_qty = self._base().allocate(1.0, Regime.BREAKOUT_WATCH, 10_000.0, 50.0)
        scaled_qty = alloc.allocate(1.0, Regime.BREAKOUT_WATCH, 10_000.0, 50.0)
        assert float(scaled_qty) == pytest.approx(float(base_qty) * 0.75, rel=1e-6)

    def test_custom_regime_factors(self):
        custom_factors = {
            Regime.TRENDING: 2.0,
            Regime.RANGING: 0.1,
            Regime.BREAKOUT_WATCH: 0.0,
        }
        alloc = RegimeScaledAllocator(self._base(), regime_factors=custom_factors)
        base_qty = float(self._base().allocate(1.0, Regime.TRENDING, 10_000.0, 50.0))

        assert float(alloc.allocate(1.0, Regime.TRENDING, 10_000.0, 50.0)) == pytest.approx(base_qty * 2.0, rel=1e-6)
        assert float(alloc.allocate(1.0, Regime.RANGING, 10_000.0, 50.0)) == pytest.approx(base_qty * 0.1, rel=1e-6)
        assert float(alloc.allocate(1.0, Regime.BREAKOUT_WATCH, 10_000.0, 50.0)) == pytest.approx(0.0, abs=1e-8)

    def test_zero_from_base_stays_zero(self):
        alloc = RegimeScaledAllocator(self._base())
        qty = alloc.allocate(1.0, Regime.TRENDING, equity=0.0, atr=50.0)
        assert qty == Decimal("0")

    def test_returns_decimal(self):
        alloc = RegimeScaledAllocator(self._base())
        qty = alloc.allocate(1.0, Regime.RANGING, 10_000.0, 50.0)
        assert isinstance(qty, Decimal)

    def test_scaled_qty_less_than_base_for_ranging(self):
        alloc = RegimeScaledAllocator(self._base())
        base_qty = self._base().allocate(1.0, Regime.RANGING, 10_000.0, 50.0)
        scaled_qty = alloc.allocate(1.0, Regime.RANGING, 10_000.0, 50.0)
        assert scaled_qty < base_qty

    def test_missing_regime_in_custom_factors_defaults_to_1x(self):
        """If a regime isn't in the custom factors dict, fall back to factor=1.0."""
        partial_factors = {Regime.TRENDING: 0.5}  # RANGING and BREAKOUT_WATCH not specified
        alloc = RegimeScaledAllocator(self._base(), regime_factors=partial_factors)
        base_qty = float(self._base().allocate(1.0, Regime.RANGING, 10_000.0, 50.0))
        scaled_qty = float(alloc.allocate(1.0, Regime.RANGING, 10_000.0, 50.0))
        assert scaled_qty == pytest.approx(base_qty * 1.0, rel=1e-6)
