"""History page — list past runs and drill into trades/metrics."""

from __future__ import annotations

import streamlit as st

from cryptobot.analytics.queries import (
    daily_summary,
    get_equity_curve,
    get_fills_with_orders,
    list_runs,
    reconstruct_trades,
    symbol_breakdown,
    strategy_breakdown,
    fee_impact,
)
from cryptobot.analytics.report import build_report
from cryptobot.dashboard._shared import get_db_url
from cryptobot.journal.writer import build_engine, make_session_factory

st.title("🗂️ History")

db_url = get_db_url()

try:
    engine = build_engine(db_url)
    sf = make_session_factory(engine)
    runs = list_runs(sf, n=50)
except Exception as exc:
    st.error(f"Could not connect to journal: {exc}")
    st.stop()

if not runs:
    st.info("No runs found.  Run a backtest or paper session first.")
    st.stop()

# ---------------------------------------------------------------------------
# Run list table
# ---------------------------------------------------------------------------
import pandas as pd

st.subheader("Recent runs")
df_runs = pd.DataFrame(
    [
        {
            "run_id": r.id,
            "mode": r.mode,
            "strategy": r.strategy_name,
            "started": r.started_at.strftime("%Y-%m-%d %H:%M") if r.started_at else "",
            "ended": r.ended_at.strftime("%Y-%m-%d %H:%M") if r.ended_at else "running",
            "notes": (r.notes or "")[:60],
        }
        for r in runs
    ]
)
st.dataframe(df_runs, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Drill-down
# ---------------------------------------------------------------------------
run_ids = [r.id for r in runs]
selected_run_id = st.selectbox("Drill into run", run_ids)

if not selected_run_id:
    st.stop()

st.divider()
st.subheader(f"`{selected_run_id}`")

try:
    fills_with_orders = get_fills_with_orders(sf, selected_run_id)
    trades = reconstruct_trades(fills_with_orders)
    curve = get_equity_curve(sf, selected_run_id)
except Exception as exc:
    st.error(f"Could not load run data: {exc}")
    st.stop()

# Equity curve
if curve:
    st.markdown("**Equity curve**")
    st.line_chart(pd.DataFrame({"equity": curve}), y="equity", use_container_width=True)

if not trades:
    st.info("No completed trades for this run.")
    st.stop()

# Summary report
with st.expander("Full report"):
    try:
        report = build_report(selected_run_id, db_url)
        st.text(report.summary)
    except Exception as exc:
        st.warning(f"Report unavailable: {exc}")

# Trades table
st.markdown("**Trades**")
df_trades = pd.DataFrame(
    [
        {
            "symbol": t.symbol,
            "entry": t.entry_ts.strftime("%Y-%m-%d %H:%M") if t.entry_ts else "",
            "exit": t.exit_ts.strftime("%Y-%m-%d %H:%M") if t.exit_ts else "",
            "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price),
            "qty": float(t.qty),
            "gross_pnl": round(float(t.gross_pnl), 4),
            "fees": round(float(t.fees), 4),
            "net_pnl": round(float(t.net_pnl), 4),
            "win": "✓" if t.is_win else "✗",
        }
        for t in trades
    ]
)
st.dataframe(df_trades, use_container_width=True)

# Daily PnL
daily = daily_summary(trades)
if daily:
    st.markdown("**Daily PnL**")
    df_daily = pd.DataFrame(
        [
            {
                "date": d.date.isoformat(),
                "realized_pnl": round(float(d.realized_pnl), 4),
                "fees": round(float(d.fees), 4),
                "n_trades": d.n_trades,
            }
            for d in daily
        ]
    )
    st.dataframe(df_daily, use_container_width=True)

# Breakdowns
col_sym, col_strat = st.columns(2)
with col_sym:
    sym_stats = symbol_breakdown(trades)
    if sym_stats:
        st.markdown("**By symbol**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "symbol": s.symbol,
                        "n_trades": s.n_trades,
                        "net_pnl": round(float(s.net_pnl), 4),
                        "win_rate": f"{s.win_rate:.1%}",
                    }
                    for s in sym_stats
                ]
            ),
            use_container_width=True,
        )

with col_strat:
    strat_stats = strategy_breakdown(trades)
    if strat_stats:
        st.markdown("**By strategy**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "strategy": s.strategy,
                        "n_trades": s.n_trades,
                        "net_pnl": round(float(s.net_pnl), 4),
                        "win_rate": f"{s.win_rate:.1%}",
                    }
                    for s in strat_stats
                ]
            ),
            use_container_width=True,
        )

# Fee impact
fi = fee_impact(trades)
st.markdown(
    f"**Fee impact** — total fees: `${float(fi.total_fees):,.4f}` | "
    f"gross profit: `${float(fi.gross_profit):,.4f}` | "
    f"fees as % of gross: `{float(fi.fees_as_pct_of_gross):.1%}`"
)
