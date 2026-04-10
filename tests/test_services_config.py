"""Tests for cryptobot.services.config_service."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cryptobot.services.config_service import (
    EDITABLE_FIELDS,
    load_run_config,
    save_run_config,
)

# Minimal valid YAML that satisfies RunConfig.
_VALID_YAML = """
mode: backtest
market:
  symbols:
    - BTC/USDT
  timeframe: 1h
strategy:
  name: sma_crossover
  params:
    fast: 10
    slow: 30
risk:
  max_position_pct: 0.05
  symbol_allow_list:
    - BTC/USDT
fees:
  taker_bps: 10.0
starting_cash: 5000.0
warmup_bars: 50
""".strip()


def _write_config(path: Path, content: str = _VALID_YAML) -> Path:
    cfg = path / "run.yaml"
    cfg.write_text(content, encoding="utf-8")
    return cfg


# ---------------------------------------------------------------------------
# load_run_config
# ---------------------------------------------------------------------------

def test_load_run_config_valid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _write_config(tmp_path)
    rc = load_run_config(cfg)
    assert rc is not None
    assert rc.starting_cash == 5000.0
    assert rc.market.symbols == ["BTC/USDT"]


def test_load_run_config_returns_none_for_invalid_yaml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: valid: yaml: [[[", encoding="utf-8")
    rc = load_run_config(bad)
    assert rc is None


def test_load_run_config_returns_none_for_missing_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    rc = load_run_config(tmp_path / "nonexistent.yaml")
    assert rc is None


# ---------------------------------------------------------------------------
# save_run_config
# ---------------------------------------------------------------------------

def test_save_run_config_writes_custom_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _write_config(tmp_path)
    dest = save_run_config(cfg, {"starting_cash": 99999.0})
    assert dest.name == "run.custom.yaml"
    assert dest.exists()
    raw = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert raw["starting_cash"] == 99999.0


def test_save_run_config_does_not_overwrite_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _write_config(tmp_path)
    original_content = cfg.read_text(encoding="utf-8")
    save_run_config(cfg, {"starting_cash": 1.0})
    assert cfg.read_text(encoding="utf-8") == original_content


def test_save_run_config_rejects_non_allowlisted_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _write_config(tmp_path)
    # "mode" is not in EDITABLE_FIELDS — should be silently dropped
    dest = save_run_config(cfg, {"mode": "live", "starting_cash": 100.0})
    raw = yaml.safe_load(dest.read_text(encoding="utf-8"))
    # mode should remain "backtest" (unchanged)
    assert raw.get("mode") == "backtest"
    assert raw["starting_cash"] == 100.0


def test_save_run_config_raises_on_invalid_merged_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _write_config(tmp_path)
    # starting_cash must be a positive number; pass a string to break pydantic
    with pytest.raises(ValueError, match="validation"):
        save_run_config(cfg, {"starting_cash": "not-a-number"})


# ---------------------------------------------------------------------------
# EDITABLE_FIELDS allowlist sanity checks
# ---------------------------------------------------------------------------

def test_editable_fields_contains_expected_keys() -> None:
    assert "starting_cash" in EDITABLE_FIELDS
    assert "risk.max_position_pct" in EDITABLE_FIELDS
    assert "strategy.params" in EDITABLE_FIELDS
    assert "fees.taker_bps" in EDITABLE_FIELDS


def test_editable_fields_excludes_env_keys() -> None:
    # Env-level secrets must never appear in the allowlist.
    for key in EDITABLE_FIELDS:
        assert "api_key" not in key
        assert "api_secret" not in key
        assert "db_url" not in key
        assert "telegram" not in key
        assert "kill_switch" not in key
