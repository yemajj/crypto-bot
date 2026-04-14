"""Backtest page — pick config + CSV, run, display results."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from cryptobot.dashboard._shared import config_picker, get_db_url
from cryptobot.analytics.queries import (
    get_fills_with_orders,
    get_equity_curve,
    reconstruct_trades,
    daily_summary,
    symbol_breakdown,
)
from cryptobot.journal.writer import build_engine, make_session_factory
from cryptobot.services import run_service

st.title("🔁 Backtest")

db_url = get_db_url()
config_path = config_picker("Backtest config")

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
data_path_str = st.text_input(
    "OHLCV CSV path",
    placeholder="data/BTC_USDT_1h.csv",
    help="Path to Binance-format OHLCV CSV (open_time, open, high, low, close, volume).",
)

run_bt = st.button("▶ Run backtest", type="primary", disabled=(config_path is None or not data_path_str))

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if run_bt:
    data_path = Path(data_path_str)
    if not data_path.exists():
        st.error(f"CSV not found: `{data_path}`")
    else:
        with st.spinner("Running backtest…"):
            try:
                run_id = run_service.run_backtest_sync(config_path, data_path)
                st.session_state["bt_run_id"] = run_id
                st.success(f"Backtest complete: `{run_id}`")
            except Exception as exc:
                st.error(f"Backtest failed: {exc}")

# ---------------------------------------------------------------------------
# Results for most-recently-run backtest in this session
# ---------------------------------------------------------------------------
run_id: str | None = st.session_state.get("bt_run_id")

if run_id:
    st.subheader(f"Results — `{run_id}`")
    try:
        engine = build_engine(db_url)
        sf = make_session_factory(engine)

        # Equity curve + metrics summary
        curve = get_equity_curve(sf, run_id)
        fills_with_orders = get_fills_with_orders(sf, run_id)
        trades = reconstruct_trades(fills_with_orders)

        if curve:
            import pandas as pd
            st.markdown("**Equity curve**")
            st.line_chart(pd.DataFrame({"equity": curve}), y="equity", use_container_width=True)

        # Metrics summary widget
        if curve and len(curve) >= 2:
            from cryptobot.backtest.metrics import ClosedTrade, compute_metrics

            ct_list = [
                ClosedTrade(
                    symbol=t.symbol,
                    entry_price=float(t.entry_price),
                    exit_price=float(t.exit_price),
                    qty=float(t.qty),
                    pnl=float(t.net_pnl),
                    n_bars=0,
                )
                for t in trades
            ]
            # Use a default timeframe for annualisation; 1h is a safe fallback.
            m = compute_metrics([10_000.0] + curve, ct_list, "1h")

            st.markdown("**Performance metrics**")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Sharpe", f"{m.sharpe:.2f}")
            col2.metric("Sortino", "999+" if m.sortino >= 999 else f"{m.sortino:.2f}")
            col3.metric("Calmar", f"{m.calmar:.2f}")
            col4.metric("Max Drawdown", f"{m.max_drawdown * 100:.1f}%")

            col5, col6, col7, col8 = st.columns(4)
            col5.metric("Hit Rate", f"{m.hit_rate * 100:.1f}%")
            col6.metric("Profit Factor", f"{m.profit_factor:.2f}")
            col7.metric("Max Consec. Losses", str(m.max_consecutive_losses))
            col8.metric("Trades", str(m.n_trades))

        if trades:
            import pandas as pd
            st.markdown("**Trades**")
            df = pd.DataFrame(
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
            st.dataframe(df, use_container_width=True)

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

            # Symbol breakdown
            sym_stats = symbol_breakdown(trades)
            if sym_stats:
                st.markdown("**By symbol**")
                df_sym = pd.DataFrame(
                    [
                        {
                            "symbol": s.symbol,
                            "n_trades": s.n_trades,
                            "net_pnl": round(float(s.net_pnl), 4),
                            "win_rate": f"{s.win_rate:.1%}",
                            "total_fees": round(float(s.total_fees), 4),
                        }
                        for s in sym_stats
                    ]
                )
                st.dataframe(df_sym, use_container_width=True)
        else:
            st.info("No completed trades found for this run.")

    except Exception as exc:
        st.error(f"Could not load results: {exc}")
