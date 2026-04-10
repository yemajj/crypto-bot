"""Home page — run status card and kill-switch toggle."""

from __future__ import annotations

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from cryptobot.dashboard._shared import get_db_url, get_kill_switch_path
from cryptobot.services import run_service

st.title("🏠 Home")

# Auto-refresh every 5 s while a run is active.
st_autorefresh(interval=5_000, limit=None, key="home_autorefresh")

db_url = get_db_url()
ks_path = get_kill_switch_path()
status = run_service.paper_status()

# ---------------------------------------------------------------------------
# Run status card
# ---------------------------------------------------------------------------
st.subheader("Run Status")

col1, col2, col3 = st.columns(3)
col1.metric("Status", "🟢 Active" if status.active else "⚫ Idle")
col2.metric("Run ID", status.run_id or "—")
col3.metric("Equity", f"${status.equity:,.2f}" if status.equity is not None else "—")

if status.error:
    st.error(f"Last run error:\n```\n{status.error}\n```")

# ---------------------------------------------------------------------------
# Kill-switch toggle
# ---------------------------------------------------------------------------
st.subheader("Kill Switch")

ks_active = run_service.kill_switch_active(ks_path)
col_a, col_b = st.columns([1, 4])

with col_a:
    if ks_active:
        st.error("🔴 ARMED")
    else:
        st.success("🟢 Disarmed")

with col_b:
    if ks_active:
        if st.button("Disarm kill switch", type="primary"):
            run_service.kill_switch_disarm(ks_path)
            st.rerun()
    else:
        if st.button("Arm kill switch", type="secondary"):
            run_service.kill_switch_arm(ks_path)
            st.rerun()

st.caption(f"Kill-switch file: `{ks_path}`")
