"""Read-only analytics queries against the trade journal.

All functions accept a SQLAlchemy session_factory and return plain dataclasses —
no side effects, no printing.

Trade reconstruction uses FIFO matching per symbol: BUY fills open a position,
SELL fills close it. `n_bars_held` is not available from the DB (no bar-level
timestamps), so it is set to 0 for DB-sourced trades.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from cryptobot.journal.models import EquitySnapshotRow, FillRow, OrderRow, Run


# ---------------------------------------------------------------------------
# Public dataclasses returned by queries
# ---------------------------------------------------------------------------


@dataclass
class TradeRecord:
    symbol: str
    strategy: str
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float    # (exit - entry) * qty
    fees: float         # total fees on entry + exit fills
    net_pnl: float      # gross_pnl - fees
    entry_ts: datetime
    exit_ts: datetime
    is_win: bool


@dataclass
class DailySummary:
    date: date
    realized_pnl: float
    fees: float
    n_trades: int


@dataclass
class SymbolStats:
    symbol: str
    n_trades: int
    net_pnl: float
    total_fees: float
    win_rate: float


@dataclass
class StrategyStats:
    strategy: str
    n_trades: int
    net_pnl: float
    total_fees: float
    win_rate: float


@dataclass
class FeeImpact:
    total_fees: float
    gross_profit: float          # sum of positive gross_pnl only
    fees_as_pct_of_gross: float  # 0.0 if no gross profit


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------


def get_run(session_factory: sessionmaker, run_id: str) -> Run | None:
    with session_factory() as s:
        return s.get(Run, run_id)


def list_runs(session_factory: sessionmaker, n: int = 20) -> list[Run]:
    """Return the n most-recent runs (newest first)."""
    with session_factory() as s:
        rows = s.execute(
            select(Run).order_by(Run.started_at.desc()).limit(n)
        ).scalars().all()
        # Detach so they survive session close.
        return list(rows)


def get_fills_with_orders(
    session_factory: sessionmaker,
    run_id: str,
) -> list[tuple[OrderRow, FillRow]]:
    """Return (order, fill) pairs for a run, ordered by fill timestamp.

    Stop-loss fills reference synthetic orders not in the orders table; these
    are returned with a placeholder OrderRow (side='SELL', strategy='stop_loss').
    """
    with session_factory() as s:
        # Fills for orders that exist in the orders table.
        rows = (
            s.execute(
                select(OrderRow, FillRow)
                .join(FillRow, FillRow.order_id == OrderRow.id)
                .where(OrderRow.run_id == run_id)
                .order_by(FillRow.ts)
            )
            .all()
        )
        pairs = list(rows)

        # Also pick up fills whose order_id is not in our orders table (stop fills).
        known_order_ids = {o.id for o, _ in pairs}
        orphan_fills = (
            s.execute(
                select(FillRow)
                .where(FillRow.order_id.not_in(
                    select(OrderRow.id).where(OrderRow.run_id == run_id)
                ))
            )
            .scalars()
            .all()
        )
        for fill in orphan_fills:
            # Build a minimal stub order so callers always get (order, fill).
            stub = OrderRow()
            stub.id = fill.order_id
            stub.run_id = run_id
            stub.strategy = "stop_loss"
            stub.symbol = ""           # unknown without joining; callers may skip
            stub.side = "SELL"
            stub.qty = fill.qty
            stub.order_type = "MARKET"
            stub.limit_price = None
            stub.status = "FILLED"
            stub.ts_submitted = fill.ts
            pairs.append((stub, fill))

        pairs.sort(key=lambda p: p[1].ts)
        return pairs


# ---------------------------------------------------------------------------
# Trade reconstruction
# ---------------------------------------------------------------------------


def reconstruct_trades(
    fills_with_orders: list[tuple[OrderRow, FillRow]],
) -> list[TradeRecord]:
    """FIFO trade matching per symbol.

    Pairs BUY fills (open) with subsequent SELL fills (close). Partial fills
    are not currently emitted as separate trades; the full position is matched
    when it closes.
    """
    # State per symbol: (avg_entry_price, qty, cumulative_fees, entry_ts, strategy)
    open_positions: dict[str, tuple[float, float, float, datetime, str]] = {}
    trades: list[TradeRecord] = []

    for order, fill in fills_with_orders:
        sym = order.symbol or ""
        if not sym:
            continue  # skip stubs with no symbol

        if order.side == "BUY":
            if sym not in open_positions:
                open_positions[sym] = (
                    float(fill.price),
                    float(fill.qty),
                    float(fill.fee),
                    fill.ts,
                    order.strategy,
                )
            else:
                # Average into existing position.
                ep, eq, ef, ets, strat = open_positions[sym]
                new_qty = eq + float(fill.qty)
                new_avg = (ep * eq + float(fill.price) * float(fill.qty)) / new_qty
                open_positions[sym] = (new_avg, new_qty, ef + float(fill.fee), ets, strat)

        elif order.side == "SELL":
            if sym not in open_positions:
                # Orphaned SELL (e.g., stop on a run we don't have BUY for).
                continue
            ep, eq, ef, ets, strat = open_positions.pop(sym)
            exit_price = float(fill.price)
            qty = min(eq, float(fill.qty))  # guard against over-sell
            gross_pnl = (exit_price - ep) * qty
            total_fees = ef + float(fill.fee)
            net_pnl = gross_pnl - total_fees
            trades.append(TradeRecord(
                symbol=sym,
                strategy=strat,
                entry_price=ep,
                exit_price=exit_price,
                qty=qty,
                gross_pnl=gross_pnl,
                fees=total_fees,
                net_pnl=net_pnl,
                entry_ts=ets,
                exit_ts=fill.ts,
                is_win=net_pnl > 0,
            ))

    return trades


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------


def daily_summary(trades: list[TradeRecord]) -> list[DailySummary]:
    """Group closed trades by exit date."""
    by_date: dict[date, list[TradeRecord]] = defaultdict(list)
    for t in trades:
        by_date[t.exit_ts.date()].append(t)
    result = []
    for d in sorted(by_date):
        day_trades = by_date[d]
        result.append(DailySummary(
            date=d,
            realized_pnl=sum(t.net_pnl for t in day_trades),
            fees=sum(t.fees for t in day_trades),
            n_trades=len(day_trades),
        ))
    return result


def symbol_breakdown(trades: list[TradeRecord]) -> list[SymbolStats]:
    by_sym: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    result = []
    for sym in sorted(by_sym):
        ts = by_sym[sym]
        wins = sum(1 for t in ts if t.is_win)
        result.append(SymbolStats(
            symbol=sym,
            n_trades=len(ts),
            net_pnl=sum(t.net_pnl for t in ts),
            total_fees=sum(t.fees for t in ts),
            win_rate=wins / len(ts) if ts else 0.0,
        ))
    return result


def strategy_breakdown(trades: list[TradeRecord]) -> list[StrategyStats]:
    by_strat: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in trades:
        by_strat[t.strategy].append(t)
    result = []
    for strat in sorted(by_strat):
        ts = by_strat[strat]
        wins = sum(1 for t in ts if t.is_win)
        result.append(StrategyStats(
            strategy=strat,
            n_trades=len(ts),
            net_pnl=sum(t.net_pnl for t in ts),
            total_fees=sum(t.fees for t in ts),
            win_rate=wins / len(ts) if ts else 0.0,
        ))
    return result


def get_equity_curve(session_factory: sessionmaker, run_id: str) -> list[float]:
    """Return the equity curve for a run from stored snapshots (oldest first).

    Returns an empty list if no snapshots exist.
    """
    with session_factory() as s:
        rows = (
            s.execute(
                select(EquitySnapshotRow)
                .where(EquitySnapshotRow.run_id == run_id)
                .order_by(EquitySnapshotRow.bar_ts)
            )
            .scalars()
            .all()
        )
        return [r.equity for r in rows]


def fee_impact(trades: list[TradeRecord]) -> FeeImpact:
    total_fees = sum(t.fees for t in trades)
    gross_profit = sum(t.gross_pnl for t in trades if t.gross_pnl > 0)
    pct = (total_fees / gross_profit * 100) if gross_profit > 0 else 0.0
    return FeeImpact(
        total_fees=total_fees,
        gross_profit=gross_profit,
        fees_as_pct_of_gross=pct,
    )
