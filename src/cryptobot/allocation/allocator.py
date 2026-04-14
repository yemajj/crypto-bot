"""Allocation layer: converts a directional score into a position size (qty).

Separating *direction* (strategy score) from *size* (allocator qty) lets you:
- Scale exposure by regime without touching strategy logic.
- Swap sizing models (fixed-risk, Kelly, vol-targeting) independently.
- Test sizing rules in isolation.

ABC
---
``Allocator.allocate(score, regime, equity, atr) -> Decimal``

Concrete implementations
------------------------
``FixedRiskAllocator``
    ATR-based fixed-fractional sizing.  Risks ``risk_per_trade_pct`` of equity
    per trade, with stop distance = atr × stop_multiplier.
    This reproduces the sizing logic previously embedded in EnsembleStrategy.

``RegimeScaledAllocator``
    Wraps any base allocator and multiplies the resulting qty by a per-regime
    factor.  Default factors: TRENDING=1.0, RANGING=0.5, BREAKOUT_WATCH=0.75.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from cryptobot.core.regimes import Regime

_DEFAULT_REGIME_FACTORS: dict[Regime, float] = {
    Regime.TRENDING: 1.0,
    Regime.RANGING: 0.5,
    Regime.BREAKOUT_WATCH: 0.75,
}


class Allocator(ABC):
    """Abstract base: maps a directional score to a position qty."""

    @abstractmethod
    def allocate(
        self,
        score: float,
        regime: Regime,
        equity: float,
        atr: float,
    ) -> Decimal:
        """Return the quantity to trade.

        Parameters
        ----------
        score:
            Final directional score in [-1.0, 1.0].  Positive = bullish.
        regime:
            Current market regime from the regime detector.
        equity:
            Current portfolio equity in quote currency.
        atr:
            Current ATR for the traded symbol (same units as price).

        Returns
        -------
        Decimal
            Quantity to buy/sell.  Must be non-negative; direction is inferred
            from the intent side in the calling strategy.
        """


class FixedRiskAllocator(Allocator):
    """Risk a fixed fraction of equity per trade using ATR-based stop distance.

    qty = (equity × risk_per_trade_pct) / (atr × stop_distance_multiplier)

    Parameters
    ----------
    risk_per_trade_pct:
        Fraction of equity to risk per trade (e.g. 0.005 = 0.5%).
    stop_distance_multiplier:
        ATR multiplier for stop distance (e.g. 1.5 = 1.5 × ATR).
    """

    def __init__(
        self,
        risk_per_trade_pct: float = 0.005,
        stop_distance_multiplier: float = 1.5,
    ) -> None:
        self._risk_pct = risk_per_trade_pct
        self._stop_mult = stop_distance_multiplier

    def allocate(
        self,
        score: float,
        regime: Regime,
        equity: float,
        atr: float,
    ) -> Decimal:
        if atr <= 0.0 or equity <= 0.0:
            return Decimal("0")
        stop_distance = atr * self._stop_mult
        risk_amount = equity * self._risk_pct
        qty = risk_amount / stop_distance
        return Decimal(str(round(qty, 8)))


class RegimeScaledAllocator(Allocator):
    """Wraps a base allocator and scales qty by a per-regime factor.

    Parameters
    ----------
    base:
        Any Allocator implementation (e.g. FixedRiskAllocator).
    regime_factors:
        Optional dict mapping Regime → multiplier.  Defaults to
        TRENDING=1.0, RANGING=0.5, BREAKOUT_WATCH=0.75.
    """

    def __init__(
        self,
        base: Allocator,
        regime_factors: dict[Regime, float] | None = None,
    ) -> None:
        self._base = base
        self._factors = regime_factors if regime_factors is not None else _DEFAULT_REGIME_FACTORS

    def allocate(
        self,
        score: float,
        regime: Regime,
        equity: float,
        atr: float,
    ) -> Decimal:
        base_qty = self._base.allocate(score, regime, equity, atr)
        factor = Decimal(str(self._factors.get(regime, 1.0)))
        scaled = base_qty * factor
        return Decimal(str(round(float(scaled), 8)))
