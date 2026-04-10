"""Paper Trading page — start/stop controls and live equity chart."""

from __future__ import annotations

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from cryptobot.dashboard._shared import config_picker, get_db_url
from cryptobot.journal.writer import build_engine, make_session_factory
from cryptobot.analytics.queries import get_equity_curve
from cryptobot.services import run_service

st.title("📈 Paper Trading")

# Auto-refresh every 10 s to update equity chart.
st_autorefresh(interval=10_000, limit=None, key="paper_autorefresh")

db_url = get_db_url()
config_path = config_picker("Paper config")
status = run_service.paper_status()

# ---------------------------------------------------------------------------
# Status + controls
# ---------------------------------------------------------------------------
col1, col2, col3 = st.columns(3)
col1.metric("Status", "🟢 Active" if status.active else "⚫ Idle")
col2.metric("Run ID", status.run_id or "—")
col3.metric("Current Equity", f"${status.equity:,.2f}" if status.equity is not None else "—")

st.divider()

if status.active:
    if st.button("⏹ Stop paper run", type="primary"):
        with st.spinner("Stopping…"):
            run_service.stop_paper(timeout_s=15.0)
        st.success("Stop signal sent. The run will finish its current bar.")
        st.rerun()
else:
    if config_path is None:
        st.info("Select a config file in the sidebar to start a paper run.")
    else:
        if st.button("▶ Start paper run", type="primary", disabled=config_path is None):
            try:
                run_id = run_service.start_paper(config_path, db_url)
                st.success(f"Started paper run `{run_id}`")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))

if status.error:
    st.error(f"Last run error:\n```\n{status.error}\n```")

# ---------------------------------------------------------------------------
# Live equity chart
# ---------------------------------------------------------------------------
if status.run_id is not None:
    st.subheader("Equity curve")
    try:
        engine = build_engine(db_url)
        sf = make_session_factory(engine)
        curve = get_equity_curve(sf, status.run_id)
    except Exception as exc:
        curve = []
        st.warning(f"Could not load equity curve: {exc}")

    if curve:
        import pandas as pd
        df = pd.DataFrame({"equity": curve})
        st.line_chart(df, y="equity", use_container_width=True)
    else:
        st.info("No equity snapshots yet — waiting for the first bar to settle.")
