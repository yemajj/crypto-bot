"""Logs page — tail structured log lines for a run."""

from __future__ import annotations

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from cryptobot.dashboard._shared import get_db_url, get_log_dir
from cryptobot.analytics.queries import list_runs
from cryptobot.journal.writer import build_engine, make_session_factory
from cryptobot.services import run_service
from cryptobot.services.log_service import tail_run_log

st.title("📋 Logs")

st_autorefresh(interval=5_000, limit=None, key="logs_autorefresh")

log_dir = get_log_dir()
db_url = get_db_url()

# ---------------------------------------------------------------------------
# Run selector
# ---------------------------------------------------------------------------
status = run_service.paper_status()

engine = build_engine(db_url)
sf = make_session_factory(engine)
runs = list_runs(sf, n=20)

run_options = []
if status.active and status.run_id:
    run_options.append(f"{status.run_id} (active)")

for r in runs:
    label = r.id
    if r.id not in [o.split()[0] for o in run_options]:
        run_options.append(label)

if not run_options:
    st.info("No runs found.  Start a paper run or backtest first.")
    st.stop()

selected_label = st.selectbox("Run", run_options)
selected_run_id = selected_label.split()[0]

# ---------------------------------------------------------------------------
# Level filter
# ---------------------------------------------------------------------------
LEVELS = ["all", "error", "warning", "info", "debug"]
level_filter = st.selectbox("Minimum level", LEVELS, index=0)

n_lines = st.slider("Lines to show", min_value=20, max_value=500, value=100, step=20)

# ---------------------------------------------------------------------------
# Log display
# ---------------------------------------------------------------------------
lines = tail_run_log(selected_run_id, log_dir, n=n_lines)

LEVEL_ORDER = {"debug": 0, "info": 1, "warning": 2, "warn": 2, "error": 3, "critical": 4}
min_level = LEVEL_ORDER.get(level_filter, 0)

if level_filter != "all":
    lines = [
        ln for ln in lines
        if LEVEL_ORDER.get(str(ln.get("level", "")).lower(), 0) >= min_level
    ]

if not lines:
    st.info(f"No log file found for `{selected_run_id}` in `{log_dir}` — or no lines match the filter.")
else:
    for ln in reversed(lines):
        level = str(ln.get("level", "info")).lower()
        ts = ln.get("timestamp") or ln.get("ts") or ""
        event = ln.get("event") or ln.get("msg") or str(ln)
        extra = {k: v for k, v in ln.items() if k not in {"level", "timestamp", "ts", "event", "msg"}}

        if level in {"error", "critical"}:
            color = "🔴"
        elif level in {"warning", "warn"}:
            color = "🟡"
        else:
            color = "⬜"

        extra_str = "  ".join(f"`{k}`={v}" for k, v in extra.items()) if extra else ""
        st.markdown(f"{color} `{ts}` **{event}**  {extra_str}")
