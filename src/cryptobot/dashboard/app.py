"""Streamlit entry point.

Run with:
    streamlit run src/cryptobot/dashboard/app.py
    # OR via the CLI:
    cryptobot dashboard
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="Crypto Bot",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Page registry (st.navigation — Streamlit ≥ 1.28)
# ---------------------------------------------------------------------------

_PAGES_DIR = Path(__file__).parent / "pages"

pages = [
    st.Page(_PAGES_DIR / "home.py", title="Home", icon="🏠", default=True),
    st.Page(_PAGES_DIR / "paper.py", title="Paper Trading", icon="📈"),
    st.Page(_PAGES_DIR / "backtest.py", title="Backtest", icon="🔁"),
    st.Page(_PAGES_DIR / "config_editor.py", title="Config Editor", icon="⚙️"),
    st.Page(_PAGES_DIR / "logs.py", title="Logs", icon="📋"),
    st.Page(_PAGES_DIR / "history.py", title="History", icon="🗂️"),
]

pg = st.navigation(pages)
pg.run()
