"""Config Editor page — read-only display (v1).

v1: display the selected config as formatted YAML.
Edit form (writes to *.custom.yaml) is a follow-on step per the plan.
"""

from __future__ import annotations

import yaml
import streamlit as st

from cryptobot.dashboard._shared import config_picker
from cryptobot.services.config_service import EDITABLE_FIELDS, load_run_config

st.title("⚙️ Config Editor")

config_path = config_picker("Config to view")
if config_path is None:
    st.stop()

run_config = load_run_config(config_path)

if run_config is None:
    st.error(
        f"Could not load `{config_path.name}` — the file may be malformed. "
        "Check the logs for details."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Read-only display
# ---------------------------------------------------------------------------
st.subheader(f"`{config_path.name}`")
st.info(
    "Config display is **read-only** in v1.  "
    "Edit the YAML directly or use the safe-edit form when it ships."
)

# Dump the pydantic model back to a dict for display.
raw_dict = run_config.model_dump()
st.code(yaml.dump(raw_dict, default_flow_style=False, sort_keys=False), language="yaml")

# ---------------------------------------------------------------------------
# Editable fields reference
# ---------------------------------------------------------------------------
with st.expander("Allowlisted editable fields (for reference)"):
    st.markdown(
        "These fields will be editable via the UI in a future update.  "
        "Everything in `EnvSettings` (API keys, DB URL, tokens) is never editable here."
    )
    for field in sorted(EDITABLE_FIELDS):
        st.markdown(f"- `{field}`")
