"""Cross-sectional Rate-of-Change Momentum (XSMOM) strategy.

Design
------
At each bar, this symbol's N-bar return is computed from ctx.history and stored
in an instance-level cache (_roc_cache).  When a single strategy instance is
shared across all symbols in the basket — as run_xsmom_backtest.py does — the
cache accumulates entries for every symbol, enabling genuine cross-sectional
ranking at each time step.

Signal logic
------------
BUY  when: rank >= (1 - top_pct)  AND  roc > 0
           top tier by recent return AND positive absolute momentum

EXIT when: roc < 0                         primary — absolute momentum negative
        OR rank < bottom_exit_pct          secondary — dropped to bottom tier

Using roc < 0 as the primary exit avoids excessive churn from rank noise in a
small basket.  bottom_exit_pct (default 0.34 = bottom third) is a secondary
filter that catches prolonged relative weakness even when absolute momentum
is flat.

Top-1 rotation note (3-symbol basket)
--------------------------------------
Discrete ranks for 3 symbols are {1/3=0.33, 2/3=0.67, 3/3=1.00}.
With top_pct=0.34 the buy threshold is 0.66, so both rank=0.67 and rank=1.0
qualify as "top tier" (up to 2 symbols eligible simultaneously).
Setting max_open_positions=1 in the risk config enforces a single active
position at a time — making this a top-1 rotation test: hold the strongest
positive-momentum symbol and rotate out when it weakens.  This is the intended
first-pass behavior.

Structural differences from failed strategies
----------------------------------------------
- Not RSI: not a 0-100 oscillator with oversold/overbought thresholds
- Not SMA crossover: no moving-average comparison
- Not Donchian: no price-channel breakout
- Not ORB: no session opening-range breakout
Signal source: inter-symbol relative rank, not a single-symbol indicator.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from cryptobot.core.types import Intent, OrderType, Side
from cryptobot.strategy.base import Strategy, StrategyContext, _compute_atr
from cryptobot.strategy.registry import register_strategy


@register_strategy("xsmom")
class CrossSectionalMomentumStrategy(Strategy):
    """Cross-sectional Rate-of-Change Momentum.

    Stateful: _roc_cache accumulates each symbol's latest N-bar return.
    A shared strategy instance (see run_xsmom_backtest.py) is required for
    genuine cross-sectional ranking; per-symbol instances only see one symbol.

    Params (all optional):
        lookback                  int    default 20    N-bar RoC window
        top_pct                   float  default 0.34  buy when rank >= (1-top_pct)
        bottom_exit_pct           float  default 0.34  exit when rank < this value
        min_symbols               int    default 2     min cache entries before ranking
        atr_window                int    default 14
        risk_per_trade_pct        float  default 0.005
        stop_distance_multiplier  float  default 1.5
        max_position_notional_pct float  default 0.08
    """

    name = "xsmom"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        # Cross-sectional cache: symbol → latest N-bar rate-of-change.
        # Updated for every symbol on each bar by the shared runner.
        self._roc_cache: dict[str, float] = {}

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        bars = ctx.history
        p = ctx.params

        lookback = int(p.get("lookback", 20))
        top_pct = float(p.get("top_pct", 0.34))
        bottom_exit_pct = float(p.get("bottom_exit_pct", 0.34))
        min_symbols = int(p.get("min_symbols", 2))
        atr_window = int(p.get("atr_window", 14))
        risk_pct = float(p.get("risk_per_trade_pct", 0.005))
        stop_mult = float(p.get("stop_distance_multiplier", 1.5))
        max_notional_pct = float(p.get("max_position_notional_pct", 0.08))

        if len(bars) < lookback + 1:
            return []

        # 1. Compute and cache this symbol's N-bar return.
        close_now = float(bars[-1].close)
        close_then = float(bars[-lookback].close)
        if close_then == 0:
            return []
        roc = (close_now - close_then) / close_then
        self._roc_cache[ctx.symbol] = roc

        # 2. Need minimum cross-sectional population before ranking.
        if len(self._roc_cache) < min_symbols:
            return []

        # 3. Percentile rank: fraction of cached symbols with roc <= this one.
        #    For n symbols with distinct values, ranks are in {1/n, 2/n, ..., 1.0}.
        all_rocs = list(self._roc_cache.values())
        rank = sum(1 for r in all_rocs if r <= roc) / len(all_rocs)

        buy_threshold = 1.0 - top_pct    # e.g. 0.66 for top_pct=0.34
        exit_threshold = bottom_exit_pct  # e.g. 0.34 = bottom third

        has_position = ctx.position.qty > Decimal("0")

        # 4. Exit: primary = negative absolute momentum; secondary = bottom tier.
        if has_position and (roc < 0.0 or rank < exit_threshold):
            return [Intent(
                strategy_id=self.name,
                symbol=ctx.symbol,
                side=Side.SELL,
                qty=ctx.position.qty,
                order_type=OrderType.MARKET,
                reason=f"xsmom exit roc={roc:.4f} rank={rank:.2f}",
            )]

        # 5. Entry: top tier and positive absolute momentum.
        if not has_position and rank >= buy_threshold and roc > 0.0:
            atr = _compute_atr(bars, atr_window)
            if atr <= 0:
                return []

            stop_distance = atr * stop_mult
            stop_price_f = close_now - stop_distance
            if stop_price_f <= 0:
                return []

            # Size by risk; cap by max notional.
            raw_qty = (ctx.equity * risk_pct) / stop_distance
            max_qty = (ctx.equity * max_notional_pct) / close_now
            qty_f = min(raw_qty, max_qty)
            if qty_f <= 0:
                return []

            qty = Decimal(str(round(qty_f, 8)))
            stop_price = Decimal(str(round(stop_price_f, 8)))

            return [Intent(
                strategy_id=self.name,
                symbol=ctx.symbol,
                side=Side.BUY,
                qty=qty,
                order_type=OrderType.MARKET,
                stop_price=stop_price,
                reason=f"xsmom entry roc={roc:.4f} rank={rank:.2f}",
            )]

        return []
