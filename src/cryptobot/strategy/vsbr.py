"""Volume-Surge Breakout (VSBR).

Entry: two conditions must be true on the same bar:
  1. Volume surge  — current bar volume >= vol_surge_mult × rolling-average volume
                     (average computed over the preceding vol_window bars, NOT
                     including the current bar, to avoid look-ahead)
  2. Price breakout — current close >= the highest close over the preceding
                      breakout_window bars (new N-bar high)

Rationale: Donchian-style breakouts fail because they fire on low-conviction
moves; requiring a simultaneous volume spike is a fundamentally different filter.
A genuine institutional-participation breakout almost always comes with elevated
volume. Low-volume breakouts — which are the majority of Donchian signals — are
filtered out.

Exit: current close drops below a short trailing EMA (trend-fading signal).
The ATR-based stop-loss is passed to the risk layer as stop_price; it acts as
a hard floor if the trailing EMA has not yet triggered.

Structural differences from failed strategies:
  - Not Donchian: Donchian fires on price channel alone; VSBR requires volume.
  - Not RSI: no oscillator threshold; no mean-reversion assumption.
  - Not XSMOM: no cross-sectional ranking; per-symbol directional breakout.
  - Not VRFMR: no range-bound regime gate; specifically seeks trending breakouts.
"""

from __future__ import annotations

from decimal import Decimal

from cryptobot.core.types import Intent, OrderType, Side
from cryptobot.strategy.base import Strategy, StrategyContext, _compute_atr
from cryptobot.strategy.registry import register_strategy


def _ema(values: list[float], window: int) -> float:
    """Exponential moving average seeded from the last 3*window values.

    Limits the lookback to 3× the window so this runs in O(window) regardless
    of how large `values` grows (the backtest engine appends bars unboundedly).
    Older bars have negligible weight (k^(3*window) ≈ 0) so truncating them
    does not meaningfully change the result.

    Returns 0.0 if fewer than `window` values are available.
    """
    if len(values) < window:
        return 0.0
    # Cap lookback to 3 * window to avoid O(n) cost on a growing history.
    lookback = values[-(3 * window):]
    k = 2.0 / (window + 1)
    ema = sum(lookback[:window]) / window
    for v in lookback[window:]:
        ema = v * k + ema * (1.0 - k)
    return ema


@register_strategy("vsbr")
class VolumeSurgeBreakoutStrategy(Strategy):
    """Volume-Surge Breakout — per-symbol, no shared state.

    Params (all optional):
        vol_window                int    default 20    bars for rolling vol average
        vol_surge_mult            float  default 2.0   current vol >= N × avg vol
        breakout_window           int    default 20    bars for rolling price high
        trail_ema_window          int    default 10    EMA window for trend-fade exit
        atr_window                int    default 14
        stop_distance_multiplier  float  default 1.5
        risk_per_trade_pct        float  default 0.005
        max_position_notional_pct float  default 0.08
    """

    name = "vsbr"

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        p = ctx.params
        vol_window     = int(p.get("vol_window", 20))
        surge_mult     = float(p.get("vol_surge_mult", 2.0))
        brk_window     = int(p.get("breakout_window", 20))
        trail_ema_win  = int(p.get("trail_ema_window", 10))
        atr_window     = int(p.get("atr_window", 14))
        stop_mult      = float(p.get("stop_distance_multiplier", 1.5))
        risk_pct       = float(p.get("risk_per_trade_pct", 0.005))
        max_notional   = float(p.get("max_position_notional_pct", 0.08))

        bars = ctx.history
        # Need vol_window preceding bars + 1 current bar, and brk_window + 1.
        # Also need trail_ema_win bars for EMA on exit.
        min_bars = max(vol_window, brk_window, trail_ema_win) + 1
        if len(bars) < min_bars:
            return []

        # Only extract the tail we actually need — the backtest engine grows
        # `history` to the full dataset, so building lists from `bars` directly
        # would be O(n) on every call.  Cap to the largest window we use.
        needed = max(vol_window, brk_window, 3 * trail_ema_win) + 2
        recent = bars[-needed:]

        closes  = [float(b.close)  for b in recent]
        volumes = [float(b.volume) for b in recent]

        close_now  = closes[-1]
        vol_now    = volumes[-1]

        # --- Rolling volume average (preceding bars only, no look-ahead) ---
        avg_vol = sum(volumes[-(vol_window + 1) : -1]) / vol_window

        # --- N-bar rolling high (preceding bars only) ---
        rolling_high = max(closes[-(brk_window + 1) : -1])

        has_position = ctx.position.qty > Decimal("0")

        # ------------------------------------------------------------------ #
        # Exit: trailing EMA — trend has faded if close drops below EMA.      #
        # Checked before entry to avoid issuing BUY and SELL on the same bar. #
        # ------------------------------------------------------------------ #
        if has_position:
            trail_ema = _ema(closes, trail_ema_win)
            if trail_ema > 0 and close_now < trail_ema:
                return [Intent(
                    strategy_id=self.name,
                    symbol=ctx.symbol,
                    side=Side.SELL,
                    qty=ctx.position.qty,
                    order_type=OrderType.MARKET,
                    reason=f"vsbr exit close={close_now:.2f} < ema={trail_ema:.2f}",
                )]
            return []

        # ------------------------------------------------------------------ #
        # Entry: volume surge AND price breakout.                              #
        # ------------------------------------------------------------------ #
        volume_surge   = avg_vol > 0 and vol_now >= surge_mult * avg_vol
        price_breakout = close_now >= rolling_high

        if not (volume_surge and price_breakout):
            return []

        atr = _compute_atr(recent, atr_window)
        if atr <= 0:
            return []

        stop_distance = atr * stop_mult
        stop_price_f  = close_now - stop_distance
        if stop_price_f <= 0:
            return []

        raw_qty = (ctx.equity * risk_pct) / stop_distance
        max_qty = (ctx.equity * max_notional) / close_now
        qty_f   = min(raw_qty, max_qty)
        if qty_f <= 0:
            return []

        return [Intent(
            strategy_id=self.name,
            symbol=ctx.symbol,
            side=Side.BUY,
            qty=Decimal(str(round(qty_f, 8))),
            order_type=OrderType.MARKET,
            stop_price=Decimal(str(round(stop_price_f, 8))),
            reason=(
                f"vsbr entry vol={vol_now:.0f} avg={avg_vol:.0f} "
                f"({vol_now/avg_vol:.1f}x) close={close_now:.2f} high={rolling_high:.2f}"
            ),
        )]
