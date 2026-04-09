"""Factory for assembling RiskState from run-loop inputs.

Both run_paper.py and backtest/engine.py use this so that adding a new
RiskState field only requires one place to update.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal

from cryptobot.risk.rules import RiskState


def build_risk_state(
    *,
    equity: float,
    cash: float,
    daily_pnl: float,
    order_timestamps: deque[datetime],
    bar_ts: datetime,
    open_positions: dict[str, Decimal],
    mark_prices: dict[str, float],
    consecutive_losses: int = 0,
) -> RiskState:
    """Assemble a RiskState from run-loop state.

    `order_timestamps` is mutated in-place: entries older than 60 s before
    `bar_ts` are pruned so the caller's deque stays current.

    Args:
        equity:             current portfolio equity (cash + open position value).
        cash:               cash balance only (no open position value).
        daily_pnl:          equity − day_start_equity for the current day.
        order_timestamps:   deque of bar timestamps for filled/accepted orders in
                            the last 60 s; pruned here before counting.
        bar_ts:             timestamp of the current bar (used for rate-limit cutoff).
        open_positions:     mapping of symbol → current qty held.
        mark_prices:        mapping of symbol → current mark price (float).
        consecutive_losses: number of consecutive losing trades so far.
    """
    cutoff = bar_ts - timedelta(seconds=60)
    while order_timestamps and order_timestamps[0] < cutoff:
        order_timestamps.popleft()

    open_by_symbol = {
        sym: 1 for sym, qty in open_positions.items() if qty > Decimal("0")
    }

    return RiskState(
        equity=equity,
        gross_exposure=equity - cash,
        daily_pnl=daily_pnl,
        orders_this_minute=len(order_timestamps),
        open_intents_by_symbol=open_by_symbol,
        consecutive_losses=consecutive_losses,
        mark_price_by_symbol=mark_prices,
    )
