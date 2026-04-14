"""Ensemble strategy — composes multiple sub-strategies via weighted voting.

Each sub-strategy contributes a score in [-1.0, 1.0] for its assigned bucket
(trend, momentum, breakout, or volatility).  An optional volume_filter applies
a confidence multiplier after the 4-bucket weighted score is computed.

The ensemble generates a BUY or SELL intent only when:
  1. The weighted final_score exceeds the buy/sell threshold, AND
  2. At least min_agreeing_buckets agree directionally with meaningful magnitude.

Sub-strategies that implement ScoringStrategy are called via signal_score().
Plain Strategy sub-strategies are called via on_bar(); their intents are mapped
to +1.0 (BUY), -1.0 (SELL), or 0.0 (no intent).  This lets SmaCrossover
participate in the ensemble without any changes to its implementation.

Config shape (strategy.params in YAML):
    strategies:
      - name: sma_crossover
        bucket: trend
        params: {fast: 20, slow: 50, atr_window: 14, risk_per_trade_pct: 0.005}
      - name: rsi
        bucket: momentum
        params: {window: 14, oversold: 30, overbought: 70}
      - ...
    volume_filter:         # optional
      strategy: volume_signal
      params: {window: 20}
      multiplier_scale: 0.25
    regime_weights:        # optional; defaults to DEFAULT_REGIME_WEIGHTS
      trending: {trend: 0.40, momentum: 0.20, breakout: 0.25, volatility: 0.15}
      ...
    buy_threshold: 0.30
    sell_threshold: -0.30
    min_agreeing_buckets: 2
    agreement_min_magnitude: 0.25
    atr_window: 14
    stop_distance_multiplier: 1.5
    risk_per_trade_pct: 0.005
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from cryptobot.allocation.allocator import Allocator, FixedRiskAllocator, RegimeScaledAllocator
from cryptobot.core.types import Intent, OrderType, Side
from cryptobot.monitoring.logging_setup import get_logger
from cryptobot.strategy.base import ScoringStrategy, Strategy, StrategyContext, _compute_atr
from cryptobot.strategy.regime_detector import Regime, detect_regime
from cryptobot.strategy.registry import get_strategy, register_strategy
from cryptobot.strategy.signal_aggregator import DEFAULT_REGIME_WEIGHTS, SignalAggregator
from cryptobot.strategy.volume_signal import VolumeSignalStrategy

log = get_logger(component="ensemble")


@register_strategy("ensemble")
class EnsembleStrategy(Strategy):
    """Multi-strategy ensemble with regime-aware weighted voting.

    See module docstring for full config reference.
    """

    bucket = "ensemble"   # not used as a directional bucket; here for symmetry

    def __init__(
        self,
        params: dict[str, Any] | None = None,
        allocator: Allocator | None = None,
    ) -> None:
        super().__init__(params)
        cfg = self.params

        raw_strategies = cfg.get("strategies", [])
        if not raw_strategies:
            raise ValueError("EnsembleStrategy requires at least one sub-strategy in params['strategies']")

        # Build sub-strategy list: (bucket, instance, sub_params)
        self._subs: list[tuple[str, Strategy, dict]] = []
        for entry in raw_strategies:
            name: str = entry["name"]
            bucket: str = entry.get("bucket", "unknown")
            sub_params: dict = dict(entry.get("params", {}))
            cls = get_strategy(name)
            instance = cls(sub_params)
            self._subs.append((bucket, instance, sub_params))

        # Optional volume filter
        self._vol_strategy: VolumeSignalStrategy | None = None
        self._vol_params: dict = {}
        vol_cfg = cfg.get("volume_filter")
        if vol_cfg:
            vol_params = dict(vol_cfg.get("params", {}))
            self._vol_strategy = VolumeSignalStrategy(vol_params)
            self._vol_params = vol_params

        # Aggregator
        regime_weights = cfg.get("regime_weights")
        # Parse nested dicts if they came from YAML (already dicts)
        if regime_weights is None:
            regime_weights = DEFAULT_REGIME_WEIGHTS

        vol_scale = float(vol_cfg.get("multiplier_scale", 0.25)) if vol_cfg else 0.25

        self._aggregator = SignalAggregator(
            regime_weights=regime_weights,
            buy_threshold=float(cfg.get("buy_threshold", 0.30)),
            sell_threshold=float(cfg.get("sell_threshold", -0.30)),
            min_agreeing_buckets=int(cfg.get("min_agreeing_buckets", 2)),
            agreement_min_magnitude=float(cfg.get("agreement_min_magnitude", 0.25)),
            volume_multiplier_scale=vol_scale,
        )

        self._atr_window = int(cfg.get("atr_window", 14))
        self._stop_multiplier = float(cfg.get("stop_distance_multiplier", 1.5))
        self._risk_pct = float(cfg.get("risk_per_trade_pct", 0.005))

        # Allocation layer — injected or built from config params
        if allocator is not None:
            self._allocator: Allocator = allocator
        else:
            base = FixedRiskAllocator(
                risk_per_trade_pct=self._risk_pct,
                stop_distance_multiplier=self._stop_multiplier,
            )
            regime_cfg = cfg.get("regime_allocation")
            if regime_cfg:
                factors = {Regime(k): float(v) for k, v in regime_cfg.items()}
                self._allocator = RegimeScaledAllocator(base, regime_factors=factors)
            else:
                self._allocator = RegimeScaledAllocator(base)

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        # --- Collect bucket scores ---
        bucket_scores: dict[str, float] = {}

        for bucket, sub, sub_params in self._subs:
            sub_ctx = StrategyContext(
                symbol=ctx.symbol,
                history=ctx.history,
                position=ctx.position,
                equity=ctx.equity,
                params=sub_params,
            )

            if isinstance(sub, ScoringStrategy):
                score = sub.signal_score(sub_ctx)
            else:
                # Plain Strategy: map intent side to score
                intents = sub.on_bar(sub_ctx)
                if not intents:
                    score = 0.0
                else:
                    sides = {i.side for i in intents}
                    if Side.BUY in sides:
                        score = 1.0
                    elif Side.SELL in sides:
                        score = -1.0
                    else:
                        score = 0.0

            # Multiple sub-strategies in the same bucket: average scores.
            if bucket in bucket_scores:
                bucket_scores[bucket] = (bucket_scores[bucket] + score) / 2.0
            else:
                bucket_scores[bucket] = score

        # --- Volume confidence multiplier ---
        volume_score = 0.0
        if self._vol_strategy is not None:
            vol_ctx = StrategyContext(
                symbol=ctx.symbol,
                history=ctx.history,
                position=ctx.position,
                equity=ctx.equity,
                params=self._vol_params,
            )
            volume_score = self._vol_strategy.signal_score(vol_ctx)

        # --- Regime detection ---
        regime = detect_regime(ctx.history)

        # --- Aggregate ---
        result = self._aggregator.aggregate(bucket_scores, regime, volume_score=volume_score)

        log.debug(
            "ensemble_bar",
            symbol=ctx.symbol,
            regime=regime.value,
            bucket_scores={b: round(s, 3) for b, s in bucket_scores.items()},
            volume_score=round(volume_score, 3),
            raw_score=round(result.raw_score, 3),
            final_score=round(result.final_score, 3),
            agreement_ok=result.agreement_ok,
        )

        if not result.agreement_ok:
            return []

        atr = _compute_atr(ctx.history, self._atr_window)
        if atr == 0.0:
            return []

        buy_threshold = float(self.params.get("buy_threshold", 0.30))
        sell_threshold = float(self.params.get("sell_threshold", -0.30))

        if result.final_score >= buy_threshold and ctx.position.qty <= 0:
            stop_distance = atr * self._stop_multiplier
            current_close = float(ctx.history[-1].close)
            qty = self._allocator.allocate(result.final_score, regime, ctx.equity, atr)

            # Cap notional to max_position_notional_pct of equity (avoids risk-rule rejections).
            max_notional_pct = float(self.params.get("max_position_notional_pct", 1.0))
            if current_close > 0:
                max_qty = Decimal(str(round((ctx.equity * max_notional_pct) / current_close, 8)))
                qty = min(qty, max_qty)

            stop_price = Decimal(str(round(current_close - stop_distance, 8)))

            reason = (
                f"ensemble score={result.final_score:.3f} regime={regime.value} "
                + " ".join(f"{b}={s:.2f}" for b, s in sorted(result.bucket_scores.items()))
            )
            return [Intent(
                strategy_id="ensemble",
                symbol=ctx.symbol,
                side=Side.BUY,
                qty=qty,
                order_type=OrderType.MARKET,
                stop_price=stop_price,
                reason=reason,
            )]

        if result.final_score <= sell_threshold and ctx.position.qty > 0:
            reason = (
                f"ensemble score={result.final_score:.3f} regime={regime.value}"
            )
            return [Intent(
                strategy_id="ensemble",
                symbol=ctx.symbol,
                side=Side.SELL,
                qty=ctx.position.qty,
                order_type=OrderType.MARKET,
                reason=reason,
            )]

        return []
