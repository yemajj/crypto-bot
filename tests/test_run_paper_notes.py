from __future__ import annotations

from pathlib import Path

from cryptobot.app.run_paper import _build_run_notes
from cryptobot.config.settings import (
    EnvSettings,
    FeesConfig,
    MarketConfig,
    RiskConfig,
    RunConfig,
    Settings,
    StrategyConfig,
)


def _settings() -> Settings:
    return Settings(
        env=EnvSettings(
            cryptobot_env="dev",
            exchange_name="binanceus",
            db_url="sqlite:///:memory:",
            data_dir="data",
            log_dir="logs",
            kill_switch_file="KILL_SWITCH",
        ),
        run=RunConfig(
            mode="paper",
            market=MarketConfig(symbols=["BTC/USDT"], timeframe="5m"),
            strategy=StrategyConfig(name="sma_crossover", params={"fast": 20, "slow": 50}),
            risk=RiskConfig(symbol_allow_list=["BTC/USDT"]),
            fees=FeesConfig(taker_bps=10.0, maker_bps=5.0, slippage_bps=5.0),
            starting_cash=10_000.0,
            warmup_bars=200,
            poll_interval_seconds=30.0,
        ),
    )


def test_build_run_notes_tags_validation_profile():
    notes = _build_run_notes(Path("config/paper_validation.yaml"), _settings())
    assert "starting_cash=10000.00" in notes
    assert "config=paper_validation.yaml" in notes
    assert "symbol=BTC/USDT" in notes
    assert "timeframe=5m" in notes
    assert "validation_profile=paper_validation" in notes


def test_build_run_notes_omits_validation_tag_for_non_validation_config():
    notes = _build_run_notes(Path("config/paper.yaml"), _settings())
    assert "config=paper.yaml" in notes
    assert "validation_profile=paper_validation" not in notes
