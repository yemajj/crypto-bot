"""Backtest metrics (skeleton).

Phase 3 adds: total return, CAGR, Sharpe, Sortino, max drawdown, hit rate,
avg win/loss, turnover, time in market.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Metrics:
    total_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    hit_rate: float = 0.0
    n_trades: int = 0
    # TODO (Phase 3): fill these in from the equity curve + trade list.
