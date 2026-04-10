"""Shared helpers for dashboard pages (no Streamlit state in the service layer)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from cryptobot.config import load_settings


def config_picker(label: str = "Config file") -> Path | None:
    """Sidebar selectbox for choosing a YAML config.  Returns selected Path or None."""
    config_dir = Path("config")
    yamls = sorted(config_dir.glob("*.yaml")) if config_dir.exists() else []
    if not yamls:
        st.sidebar.warning("No YAML files found in config/")
        return None
    names = [f.name for f in yamls]
    selected = st.sidebar.selectbox(label, names, key="global_config_picker")
    return config_dir / selected


def get_db_url() -> str:
    """Load db_url from env settings (no config YAML required)."""
    try:
        settings = load_settings(None)
        return settings.env.db_url
    except Exception:
        return "sqlite:///./data/cryptobot.sqlite"


def get_kill_switch_path() -> Path:
    """Load kill-switch path from env settings."""
    try:
        settings = load_settings(None)
        return settings.env.kill_switch_file
    except Exception:
        return Path("./KILL_SWITCH")


def get_log_dir() -> Path:
    """Load log dir from env settings."""
    try:
        settings = load_settings(None)
        return settings.env.log_dir
    except Exception:
        return Path("./logs")
