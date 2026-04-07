from __future__ import annotations

from pathlib import Path

import pytest

from cryptobot.config import load_settings
from cryptobot.config.settings import RunConfig


def test_load_settings_without_yaml_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert isinstance(settings.run, RunConfig)
    assert settings.run.mode == "backtest"
    assert settings.run.market.symbols == ["BTC/USDT"]
    assert settings.env.env in {"dev", "paper", "live"}


def test_load_settings_reads_yaml(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "run.yaml"
    cfg.write_text(
        """
mode: backtest
market:
  symbols: [ETH/USDT]
  timeframe: 4h
strategy:
  name: sma_crossover
  params: {fast: 10, slow: 30}
risk:
  max_position_pct: 0.05
  symbol_allow_list: [ETH/USDT]
fees:
  taker_bps: 8.0
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(cfg)
    assert settings.run.market.symbols == ["ETH/USDT"]
    assert settings.run.market.timeframe == "4h"
    assert settings.run.strategy.params["fast"] == 10
    assert settings.run.risk.max_position_pct == 0.05
    assert settings.run.fees.taker_bps == 8.0


def test_load_settings_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.yaml")
