"""Backtest metrics: computed from an equity curve and a list of closed trades.

All metrics are honest-by-design:
- Sharpe uses actual bar returns (not annualised from a short window).
- Max drawdown is peak-to-trough on the equity curve, not just max single-bar loss.
- Profit factor is undefined (returns 0.0) when there are no losing trades.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import NamedTuple


class ClosedTrade(NamedTuple):
    """One complete entry + exit round-trip."""

    symbol: str
    entry_price: float
    exit_price: float
    qty: float
    pnl: float          # net of fees
    n_bars: int         # bars held


_BARS_PER_YEAR: dict[str, float] = {
    "1m": 60 * 24 * 365,
    "5m": 12 * 24 * 365,
    "15m": 4 * 24 * 365,
    "30m": 2 * 24 * 365,
    "1h": 24 * 365,
    "2h": 12 * 365,
    "4h": 6 * 365,
    "6h": 4 * 365,
    "8h": 3 * 365,
    "12h": 2 * 365,
    "1d": 365.0,
    "1w": 52.0,
}


@dataclass
class Metrics:
    total_return: float = 0.0           # (final − initial) / initial
    max_drawdown: float = 0.0           # max peak-to-trough / peak (positive number)
    sharpe: float = 0.0                 # annualised Sharpe; risk-free = 0
    sortino: float = 0.0                # annualised Sortino (downside deviation only)
    calmar: float = 0.0                 # annualised return / max drawdown; 0 if no drawdown
    profit_factor: float = 0.0          # sum(wins) / abs(sum(losses)); 0 if no losses
    hit_rate: float = 0.0               # winning trades / closed trades
    n_trades: int = 0                   # closed round-trips
    avg_trade_return: float = 0.0       # mean(pnl / entry_value) per trade
    time_in_market_pct: float = 0.0     # % of bars holding a position
    max_consecutive_losses: int = 0     # longest losing streak by trade count
    max_consecutive_wins: int = 0       # longest winning streak by trade count


def compute_metrics(
    equity_curve: list[float],
    trades: list[ClosedTrade],
    timeframe: str,
    bars_in_position: int = 0,
) -> Metrics:
    """Compute all metrics from the equity curve and closed trade list.

    Args:
        equity_curve: equity value at the close of every bar (including bar 0 =
                      starting cash).  Must have at least 2 values.
        trades:       list of ClosedTrade from the engine.
        timeframe:    bar timeframe string e.g. "1h", "4h", "1d".
        bars_in_position: total number of bars the strategy held an open
                      position (supplied by the engine).
    """
    if len(equity_curve) < 2:
        return Metrics()

    initial = equity_curve[0]
    final = equity_curve[-1]

    total_return = (final - initial) / initial if initial > 0 else 0.0

    max_drawdown = _max_drawdown(equity_curve)
    sharpe = _sharpe(equity_curve, timeframe)
    sortino = _sortino(equity_curve, timeframe)
    calmar = _calmar(equity_curve, timeframe, max_drawdown)

    n_trades = len(trades)
    hit_rate = 0.0
    avg_trade_return = 0.0
    profit_factor = 0.0
    time_pct = 0.0
    max_consecutive_losses = 0
    max_consecutive_wins = 0

    if n_trades > 0:
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl < 0]
        hit_rate = len(wins) / n_trades

        gross_wins = sum(t.pnl for t in wins)
        gross_losses = abs(sum(t.pnl for t in losses))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 0.0

        trade_returns = [
            t.pnl / (t.entry_price * t.qty) if t.entry_price > 0 and t.qty > 0 else 0.0
            for t in trades
        ]
        avg_trade_return = sum(trade_returns) / n_trades

        max_consecutive_losses, max_consecutive_wins = _consecutive_streaks(trades)

    total_bars = len(equity_curve) - 1  # bar 0 is pre-loop
    if total_bars > 0:
        time_pct = bars_in_position / total_bars * 100.0

    return Metrics(
        total_return=total_return,
        max_drawdown=max_drawdown,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        profit_factor=profit_factor,
        hit_rate=hit_rate,
        n_trades=n_trades,
        avg_trade_return=avg_trade_return,
        time_in_market_pct=time_pct,
        max_consecutive_losses=max_consecutive_losses,
        max_consecutive_wins=max_consecutive_wins,
    )


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _sharpe(equity_curve: list[float], timeframe: str) -> float:
    if len(equity_curve) < 3:
        return 0.0
    returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    if std_r == 0:
        return 0.0
    bars_per_year = _BARS_PER_YEAR.get(timeframe, 365.0)
    return (mean_r / std_r) * math.sqrt(bars_per_year)


def _sortino(equity_curve: list[float], timeframe: str) -> float:
    """Annualised Sortino ratio (downside deviation; risk-free = 0)."""
    if len(equity_curve) < 3:
        return 0.0
    returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        # No negative returns — perfect upside, Sortino is undefined; return a large sentinel.
        return 999.0
    downside_var = sum(r ** 2 for r in downside) / len(returns)
    downside_std = math.sqrt(downside_var)
    if downside_std == 0:
        return 0.0
    bars_per_year = _BARS_PER_YEAR.get(timeframe, 365.0)
    return (mean_r / downside_std) * math.sqrt(bars_per_year)


def _calmar(equity_curve: list[float], timeframe: str, max_drawdown: float) -> float:
    """Calmar ratio: annualised return / max drawdown. Returns 0 if drawdown is 0."""
    if max_drawdown == 0.0 or len(equity_curve) < 2:
        return 0.0
    initial = equity_curve[0]
    final = equity_curve[-1]
    if initial <= 0:
        return 0.0
    n_bars = len(equity_curve) - 1
    bars_per_year = _BARS_PER_YEAR.get(timeframe, 365.0)
    # Compound annualisation
    total_return = final / initial
    annualised_return = total_return ** (bars_per_year / n_bars) - 1
    return annualised_return / max_drawdown


def _consecutive_streaks(trades: list[ClosedTrade]) -> tuple[int, int]:
    """Return (max_consecutive_losses, max_consecutive_wins)."""
    max_losses = max_wins = cur_losses = cur_wins = 0
    for t in trades:
        if t.pnl > 0:
            cur_wins += 1
            cur_losses = 0
        elif t.pnl < 0:
            cur_losses += 1
            cur_wins = 0
        else:
            cur_losses = 0
            cur_wins = 0
        if cur_losses > max_losses:
            max_losses = cur_losses
        if cur_wins > max_wins:
            max_wins = cur_wins
    return max_losses, max_wins
