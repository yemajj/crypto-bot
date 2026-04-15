"""Volatility-regime filtered mean reversion (VRFMR).

Gate:   ADX < adx_threshold  →  range-bound regime, OK to trade
Signal: RSI < rsi_oversold   →  BUY (ATR-based stop)
Exit:   RSI >= rsi_exit      →  mean reversion back to mean, close position
        ADX >= adx_threshold →  trend developing, exit defensively

Structural difference from the prior RSI failure (rsi.py):
  rsi.py trades in ALL market conditions; in a downtrend, RSI < 30 fires
  repeatedly on each new leg lower, generating a string of losers.
  VRFMR uses ADX to gate entries to range-bound conditions only, and
  exits any open position if the regime flips to trending mid-trade.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from cryptobot.core.types import Intent, OrderType, Side
from cryptobot.strategy.base import Strategy, StrategyContext, _compute_atr, _compute_adx
from cryptobot.strategy.registry import register_strategy
from cryptobot.strategy.rsi import _rsi


@register_strategy("vrfmr")
class VolatilityRegimeMeanReversionStrategy(Strategy):
    """Volatility-regime filtered mean reversion.

    Per-symbol; no shared state across symbols.  The regime gate (ADX) is
    the key structural addition over simple RSI mean reversion.

    Params (all optional):
        adx_window                int    default 14
        adx_threshold             float  default 25.0   (< 25 = range-bound)
        rsi_window                int    default 14
        rsi_oversold              float  default 30.0   (entry trigger)
        rsi_exit                  float  default 50.0   (exit at midpoint)
        atr_window                int    default 14
        stop_distance_multiplier  float  default 1.5
        risk_per_trade_pct        float  default 0.005
        max_position_notional_pct float  default 0.08
    """

    name = "vrfmr"

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        p = ctx.params
        adx_window    = int(p.get("adx_window", 14))
        adx_threshold = float(p.get("adx_threshold", 25.0))
        rsi_window    = int(p.get("rsi_window", 14))
        rsi_oversold  = float(p.get("rsi_oversold", 30.0))
        rsi_exit      = float(p.get("rsi_exit", 50.0))
        atr_window    = int(p.get("atr_window", 14))
        stop_mult     = float(p.get("stop_distance_multiplier", 1.5))
        risk_pct      = float(p.get("risk_per_trade_pct", 0.005))
        max_notional  = float(p.get("max_position_notional_pct", 0.08))

        bars = ctx.history
        # ADX needs 2*window+1 bars; it is the binding warmup requirement.
        if len(bars) < 2 * adx_window + 1:
            return []

        adx = _compute_adx(bars, adx_window)
        in_regime = adx < adx_threshold

        closes = [float(b.close) for b in bars]
        rsi = _rsi(closes, rsi_window)

        has_position = ctx.position.qty > Decimal("0")
        close_now = float(bars[-1].close)

        # --- Exit (checked before entry to avoid double-signal) ---
        if has_position and (rsi >= rsi_exit or not in_regime):
            return [Intent(
                strategy_id=self.name,
                symbol=ctx.symbol,
                side=Side.SELL,
                qty=ctx.position.qty,
                order_type=OrderType.MARKET,
                reason=f"vrfmr exit rsi={rsi:.1f} adx={adx:.1f} in_regime={in_regime}",
            )]

        # --- Entry: range-bound regime AND oversold ---
        if not has_position and in_regime and rsi < rsi_oversold:
            atr = _compute_atr(bars, atr_window)
            if atr <= 0:
                return []
            stop_distance = atr * stop_mult
            stop_price_f = close_now - stop_distance
            if stop_price_f <= 0:
                return []
            raw_qty = (ctx.equity * risk_pct) / stop_distance
            max_qty = (ctx.equity * max_notional) / close_now
            qty_f = min(raw_qty, max_qty)
            if qty_f <= 0:
                return []
            return [Intent(
                strategy_id=self.name,
                symbol=ctx.symbol,
                side=Side.BUY,
                qty=Decimal(str(round(qty_f, 8))),
                order_type=OrderType.MARKET,
                stop_price=Decimal(str(round(stop_price_f, 8))),
                reason=f"vrfmr entry rsi={rsi:.1f} adx={adx:.1f}",
            )]

        return []
