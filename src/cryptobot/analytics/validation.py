"""Paper-trading validation summaries for the Phase 6 live-trading gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptobot.analytics.queries import (
    fee_impact,
    get_equity_curve,
    get_fills_with_orders,
    get_run,
    list_runs,
    reconstruct_trades,
)
from cryptobot.backtest.metrics import ClosedTrade, Metrics, compute_metrics
from cryptobot.journal.models import Run
from cryptobot.journal.writer import build_engine, make_session_factory

_VALIDATION_PROFILE = "paper_validation"
_NOTE_KV_RE = re.compile(r"([A-Za-z_]+)=([^\s]+)")


@dataclass
class ValidationRunSummary:
    run_id: str
    started_at: datetime
    ended_at: datetime
    starting_cash: float
    final_equity: float
    trade_count: int
    config: str
    symbol: str
    timeframe: str


def parse_notes_metadata(notes: str | None) -> dict[str, str]:
    """Extract key=value tokens from the journal notes field."""
    if not notes:
        return {}
    return {key: value for key, value in _NOTE_KV_RE.findall(notes)}


def is_validation_run(run: Run) -> bool:
    """Return True if a run is a completed paper-validation run."""
    if run.mode != "paper" or run.ended_at is None:
        return False
    metadata = parse_notes_metadata(run.notes)
    return metadata.get("validation_profile") == _VALIDATION_PROFILE


def build_validation_report(
    db_url: str,
    days: int = 14,
    run_ids: list[str] | None = None,
) -> str:
    """Build a multi-run validation report for completed paper runs."""
    engine = build_engine(db_url)
    session_factory = make_session_factory(engine)

    selected_runs, skipped = _select_runs(session_factory, days=days, run_ids=run_ids)
    if not selected_runs:
        scope = "requested run_ids" if run_ids else f"last {days} days"
        return (
            f"No completed validation paper runs found for {scope}.\n"
            f"Run `cryptobot paper --config config/{_VALIDATION_PROFILE}.yaml` first."
        )

    summaries: list[ValidationRunSummary] = []
    combined_trades = []
    combined_curve = _initial_combined_curve(selected_runs)

    for run in selected_runs:
        metadata = parse_notes_metadata(run.notes)
        trades = reconstruct_trades(get_fills_with_orders(session_factory, run.id))
        curve = _run_equity_curve(session_factory, run.id, run.notes, trades)

        summaries.append(ValidationRunSummary(
            run_id=run.id,
            started_at=run.started_at,
            ended_at=run.ended_at,
            starting_cash=curve[0],
            final_equity=curve[-1],
            trade_count=len(trades),
            config=metadata.get("config", "?"),
            symbol=metadata.get("symbol", "?"),
            timeframe=metadata.get("timeframe", "1h"),
        ))

        _append_curve_changes(combined_curve, curve)
        combined_trades.extend(
            ClosedTrade(
                symbol=t.symbol,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                qty=t.qty,
                pnl=t.net_pnl,
                n_bars=0,
            )
            for t in trades
        )

    metrics = compute_metrics(combined_curve, combined_trades, "1h")
    fi = fee_impact([
        t for run in selected_runs
        for t in reconstruct_trades(get_fills_with_orders(session_factory, run.id))
    ])
    total_starting_cash = sum(s.starting_cash for s in summaries)
    total_final_equity = sum(s.final_equity for s in summaries)

    lines = []
    sep = "=" * 62
    thin = "-" * 62
    lines.append(f"\n{sep}")
    lines.append(f"  PAPER VALIDATION  ({len(summaries)} runs)")
    lines.append(sep)
    lines.append(f"  Window            : {summaries[0].started_at:%Y-%m-%d} -> {summaries[-1].ended_at:%Y-%m-%d}")
    lines.append(f"  Closed trades     : {sum(s.trade_count for s in summaries):>10d}")
    lines.append(f"  Start -> final    : ${total_starting_cash:,.2f} -> ${total_final_equity:,.2f}")
    lines.append(f"  Aggregate PnL     : ${total_final_equity - total_starting_cash:+,.2f}")
    lines.append(thin)
    lines.append("  AUTOMATED CHECKS")
    for item in _gate_lines(metrics, fi):
        lines.append(f"  {item}")
    lines.append(thin)
    lines.append("  MANUAL CHECKS")
    for item in _manual_lines():
        lines.append(f"  {item}")
    lines.append(thin)
    lines.append("  CONTRIBUTING RUNS")
    for summary in summaries:
        lines.append(
            f"  {summary.run_id}  {summary.symbol} {summary.timeframe}  "
            f"{summary.trade_count} trades  ${summary.final_equity - summary.starting_cash:+,.2f}  "
            f"{summary.config}"
        )

    if skipped:
        lines.append(thin)
        lines.append("  SKIPPED RUNS")
        for item in skipped:
            lines.append(f"  {item}")

    lines.append(f"{sep}\n")
    return "\n".join(lines)


def _select_runs(session_factory, days: int, run_ids: list[str] | None) -> tuple[list[Run], list[str]]:
    skipped: list[str] = []
    if run_ids:
        runs: list[Run] = []
        for run_id in run_ids:
            run = get_run(session_factory, run_id)
            if run is None:
                skipped.append(f"{run_id} (not found)")
                continue
            if run.mode != "paper":
                skipped.append(f"{run_id} (not a paper run)")
                continue
            if run.ended_at is None:
                skipped.append(f"{run_id} (still running)")
                continue
            runs.append(run)
        runs.sort(key=lambda run: run.started_at)
        return runs, skipped

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    runs = []
    for run in list_runs(session_factory, n=500):
        if _as_utc(run.started_at) < cutoff:
            continue
        if is_validation_run(run):
            runs.append(run)
    runs.sort(key=lambda run: run.started_at)
    return runs, skipped


def _parse_starting_cash(notes: str | None) -> float:
    metadata = parse_notes_metadata(notes)
    raw = metadata.get("starting_cash")
    return float(raw) if raw is not None else 10_000.0


def _run_equity_curve(session_factory, run_id: str, notes: str | None, trades) -> list[float]:
    starting_cash = _parse_starting_cash(notes)
    db_curve = get_equity_curve(session_factory, run_id)
    if len(db_curve) >= 2:
        return [starting_cash] + db_curve

    curve = [starting_cash]
    running = starting_cash
    for trade in trades:
        running += trade.net_pnl
        curve.append(running)
    if len(curve) < 2:
        curve.append(starting_cash)
    return curve


def _initial_combined_curve(runs: list[Run]) -> list[float]:
    total_starting_cash = sum(_parse_starting_cash(run.notes) for run in runs)
    return [total_starting_cash]


def _append_curve_changes(combined_curve: list[float], run_curve: list[float]) -> None:
    prev = run_curve[0]
    current = combined_curve[-1]
    for value in run_curve[1:]:
        current += value - prev
        combined_curve.append(current)
        prev = value


def _gate_lines(metrics: Metrics, fi) -> list[str]:
    gates = [
        ("Sharpe > 1.0", metrics.sharpe >= 1.0, f"{metrics.sharpe:.2f}"),
        ("Max DD < 20%", metrics.max_drawdown < 0.20, f"{metrics.max_drawdown * 100:.1f}%"),
        (">= 30 closed trades", metrics.n_trades >= 30, str(metrics.n_trades)),
        ("Win rate > 40%", metrics.hit_rate > 0.40, f"{metrics.hit_rate * 100:.1f}%"),
        ("Fees < 15% of gross", fi.fees_as_pct_of_gross < 15.0, f"{fi.fees_as_pct_of_gross:.1f}%"),
    ]
    return [
        f"[{'OK  ' if passed else 'FAIL'}] {label:<26} (current: {current})"
        for label, passed, current in gates
    ]


def _manual_lines() -> list[str]:
    return [
        "[PENDING] Kill switch manually tested",
        "[PENDING] Daily loss halt manually tested",
        "[PENDING] Telegram alerts verified",
        "[PENDING] Paper-vs-backtest behavior reviewed",
    ]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
