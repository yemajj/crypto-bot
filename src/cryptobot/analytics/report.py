"""Per-run reporting: queries the journal DB and renders a text summary."""

from __future__ import annotations

import re
from dataclasses import dataclass

from cryptobot.analytics.queries import (
    daily_summary,
    fee_impact,
    get_equity_curve,
    get_fills_with_orders,
    get_run,
    list_runs,
    reconstruct_trades,
    symbol_breakdown,
)
from cryptobot.backtest.metrics import ClosedTrade, compute_metrics
from cryptobot.journal.writer import build_engine, make_session_factory


@dataclass
class RunReport:
    run_id: str
    summary: str


def build_report(run_id: str, db_url: str) -> RunReport:
    """Build a text performance report for a single run."""
    engine = build_engine(db_url)
    sf = make_session_factory(engine)

    run = get_run(sf, run_id)
    if run is None:
        return RunReport(run_id=run_id, summary=f"run not found: {run_id}")

    pairs = get_fills_with_orders(sf, run_id)
    trades = reconstruct_trades(pairs)
    sym_stats = symbol_breakdown(trades)
    strat_stats = symbol_breakdown(trades)  # same shape, reuse for now
    fi = fee_impact(trades)
    days = daily_summary(trades)

    # Timeframe is not stored in the DB; default to "1h" for Sharpe annualisation.
    timeframe = "1h"

    starting_cash = _parse_starting_cash(run.notes)

    # Prefer DB equity snapshots (accurate mark-to-market); fall back to
    # reconstructing from closed-trade PnL when snapshots are unavailable.
    db_curve = get_equity_curve(sf, run_id)
    if len(db_curve) >= 2:
        equity_curve = [starting_cash] + db_curve
    else:
        equity_curve = _build_equity_curve(starting_cash, trades)

    # Convert to ClosedTrade for reuse of compute_metrics.
    ct_list = [
        ClosedTrade(
            symbol=t.symbol,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            qty=t.qty,
            pnl=t.net_pnl,
            n_bars=0,
        )
        for t in trades
    ]
    metrics = compute_metrics(equity_curve, ct_list, timeframe)

    # --- Pre-live checklist ---
    checklist = _pre_live_checklist(metrics, fi)

    # --- Format ---
    sep = "\u2501" * 53
    thin = "\u2500" * 53
    start_s = run.started_at.strftime("%Y-%m-%d %H:%M") if run.started_at else "?"
    end_s = run.ended_at.strftime("%Y-%m-%d %H:%M") if run.ended_at else "running"

    lines: list[str] = []
    lines.append(f"\n{sep}")
    lines.append(f"  REPORT  {run_id}  [{run.mode}]")
    lines.append(f"  {run.strategy}  |  {start_s} \u2192 {end_s}")
    lines.append(sep)
    lines.append(f"  Starting capital : ${starting_cash:>12,.2f}")
    lines.append(f"  Final equity     : ${equity_curve[-1]:>12,.2f}")
    lines.append(f"  Total return     : {metrics.total_return * 100:>+11.1f} %")
    lines.append(thin)
    lines.append(f"  Closed trades    : {metrics.n_trades:>12d}")
    lines.append(f"  Win rate         : {metrics.hit_rate * 100:>11.1f} %")
    lines.append(f"  Profit factor    : {metrics.profit_factor:>12.2f}")
    lines.append(f"  Sharpe (annual)  : {metrics.sharpe:>12.2f}")
    lines.append(f"  Max drawdown     : {metrics.max_drawdown * 100:>11.1f} %")
    lines.append(f"  Avg trade return : {metrics.avg_trade_return * 100:>+11.2f} %")
    lines.append(f"  Time in market   : {metrics.time_in_market_pct:>11.1f} %")

    if fi.total_fees > 0:
        pct_str = f"  ({fi.fees_as_pct_of_gross:.1f}% of gross)" if fi.gross_profit > 0 else ""
        lines.append(f"  Total fees paid  : ${fi.total_fees:>12,.2f}{pct_str}")

    if sym_stats:
        lines.append(thin)
        lines.append("  BY SYMBOL")
        for ss in sym_stats:
            lines.append(
                f"  {ss.symbol:<12s}: {ss.n_trades:>3d} trades, "
                f"${ss.net_pnl:>+9.2f} net, {ss.win_rate * 100:.0f}% win"
            )

    if days:
        lines.append(thin)
        lines.append("  DAILY SUMMARY  (last 7 days)")
        for d in days[-7:]:
            lines.append(
                f"  {d.date}  : ${d.realized_pnl:>+9.2f} PnL, "
                f"${d.fees:.2f} fees, {d.n_trades} trades"
            )

    lines.append(thin)
    lines.append("  PRE-LIVE CHECKLIST")
    for item in checklist:
        lines.append(f"  {item}")
    lines.append(f"{sep}\n")

    return RunReport(run_id=run_id, summary="\n".join(lines))


def list_runs_report(db_url: str, n: int = 20) -> str:
    """Return a short table of the n most-recent runs."""
    engine = build_engine(db_url)
    sf = make_session_factory(engine)
    runs = list_runs(sf, n=n)
    if not runs:
        return "No runs found in the journal."
    lines = [f"{'RUN ID':<24} {'MODE':<10} {'STRATEGY':<22} {'STARTED':<20} NOTES"]
    lines.append("-" * 90)
    for r in runs:
        started = r.started_at.strftime("%Y-%m-%d %H:%M") if r.started_at else "?"
        notes = (r.notes or "")[:30]
        lines.append(f"{r.id:<24} {r.mode:<10} {r.strategy:<22} {started:<20} {notes}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_starting_cash(notes: str | None) -> float:
    """Extract starting cash from run notes field, or default to 10 000."""
    # notes format: "final_equity=XXXX.XX"
    # We don't store starting cash in the DB, so default to 10 000.
    # If we can read it from notes in a future version, do so here.
    if notes:
        m = re.search(r"starting_cash=([\d.]+)", notes)
        if m:
            return float(m.group(1))
    return 10_000.0


def _build_equity_curve(starting_cash: float, trades: list) -> list[float]:
    """Reconstruct equity curve from realized PnL sequence."""
    curve = [starting_cash]
    running = starting_cash
    for t in trades:
        running += t.net_pnl
        curve.append(running)
    if len(curve) < 2:
        curve.append(starting_cash)
    return curve


def _pre_live_checklist(metrics, fi: "FeeImpact") -> list[str]:
    gates = [
        ("Sharpe > 1.0",        metrics.sharpe >= 1.0,          f"{metrics.sharpe:.2f}"),
        ("Max DD < 20%",        metrics.max_drawdown < 0.20,    f"{metrics.max_drawdown * 100:.1f}%"),
        (">= 30 closed trades", metrics.n_trades >= 30,         str(metrics.n_trades)),
        ("Win rate > 40%",      metrics.hit_rate > 0.40,        f"{metrics.hit_rate * 100:.1f}%"),
        ("Fees < 15% of gross", fi.fees_as_pct_of_gross < 15.0, f"{fi.fees_as_pct_of_gross:.1f}%"),
    ]
    items = []
    for label, passed, current in gates:
        tick = "OK  " if passed else "FAIL"
        items.append(f"[{tick}] {label:<26} (current: {current})")
    return items
