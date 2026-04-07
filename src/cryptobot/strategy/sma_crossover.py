"""SMA crossover starter strategy.

Long-only. Enters on a bullish SMA crossover, exits on a bearish crossover
or when the ATR-based stop-loss is hit. Position sized so that a 2×ATR
adverse move equals `risk_per_trade_pct` of current equity.

Params (all optional with defaults):
    fast             : int   — fast SMA window (default 20)
    slow             : int   — slow SMA window (default 50)
    atr_window       : int   — ATR window for stop distance + sizing (default 14)
    risk_per_trade_pct: float — fraction of equity at risk per trade (default 0.005)
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from cryptobot.core.types import Intent, OrderType, Position, Side
from cryptobot.strategy.base import Strategy, StrategyContext
from cryptobot.strategy.registry import register_strategy

# Minimum qty guard — avoids dust orders; should match exchange minimum.
_MIN_QTY = Decimal("0.00001")


def _sma(values: list[float], window: int) -> float:
    return sum(values[-window:]) / window


def _atr(bars: list, window: int) -> float:
    """Average True Range over the most recent `window` bars.

    Requires at least `window + 1` bars (one extra for prev_close).
    Returns 0.0 if there is not enough data.
    """
    if len(bars) < window + 1:
        return 0.0
    true_ranges: list[float] = []
    for i in range(len(bars) - window, len(bars)):
        b = bars[i]
        prev_close = float(bars[i - 1].close)
        high = float(b.high)
        low = float(b.low)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    return sum(true_ranges) / len(true_ranges)


def _parse_params(params: dict[str, Any]) -> tuple[int, int, int, float]:
    fast = int(params.get("fast", 20))
    slow = int(params.get("slow", 50))
    atr_window = int(params.get("atr_window", 14))
    risk_pct = float(params.get("risk_per_trade_pct", 0.005))
    return fast, slow, atr_window, risk_pct


@register_strategy("sma_crossover")
class SmaCrossover(Strategy):
    """Single-asset long-only SMA crossover with ATR-based sizing and stop-loss."""

    name = "sma_crossover"

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        fast, slow, atr_window, risk_pct = _parse_params(ctx.params)

        # Need enough history: slow SMA needs `slow` bars, plus one prior bar
        # for crossover detection, plus `atr_window + 1` bars for ATR.
        min_bars = max(slow, atr_window + 1) + 1
        if len(ctx.history) < min_bars:
            return []

        closes = [float(b.close) for b in ctx.history]

        fast_now = _sma(closes, fast)
        fast_prev = _sma(closes[:-1], fast)
        slow_now = _sma(closes, slow)
        slow_prev = _sma(closes[:-1], slow)

        in_position = ctx.position.qty > Decimal("0")
        current_bar = ctx.history[-1]
        close = float(current_bar.close)

        # --- Entry: bullish crossover ---
        bullish_crossover = (fast_prev <= slow_prev) and (fast_now > slow_now)

        if not in_position and bullish_crossover:
            atr = _atr(ctx.history, atr_window)
            if atr <= 0 or close <= 0:
                return []

            stop_distance = 2.0 * atr          # quote-currency distance
            risk_dollars = ctx.equity * risk_pct
            qty_float = risk_dollars / stop_distance  # base-currency units
            qty = Decimal(str(math.floor(qty_float * 1e5) / 1e5))  # floor to 5 decimals

            if qty < _MIN_QTY:
                return []

            stop_price = Decimal(str(round(close - stop_distance, 8)))

            return [
                Intent(
                    strategy_id=self.name,
                    symbol=ctx.symbol,
                    side=Side.BUY,
                    qty=qty,
                    order_type=OrderType.MARKET,
                    stop_price=stop_price,
                    reason=(
                        f"bullish crossover: fast={fast_now:.2f} > slow={slow_now:.2f}"
                        f", atr={atr:.2f}, stop={float(stop_price):.2f}"
                    ),
                )
            ]

        # --- Exit: bearish crossover ---
        bearish_crossover = (fast_prev >= slow_prev) and (fast_now < slow_now)

        if in_position and bearish_crossover:
            return [
                Intent(
                    strategy_id=self.name,
                    symbol=ctx.symbol,
                    side=Side.SELL,
                    qty=ctx.position.qty,
                    order_type=OrderType.MARKET,
                    reason=(
                        f"bearish crossover: fast={fast_now:.2f} < slow={slow_now:.2f}"
                    ),
                )
            ]

        return []
