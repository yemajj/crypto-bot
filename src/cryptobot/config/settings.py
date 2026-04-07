"""Layered configuration.

Precedence (highest first):
1. Environment variables (loaded from `.env` via pydantic-settings).
2. A YAML file under `config/` (strategy params, symbols, risk caps).

Secrets live ONLY in env. YAML files are safe to commit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# --- env-driven settings (secrets + runtime) ---------------------------------


class EnvSettings(BaseSettings):
    """Environment-variable settings. Secrets live here, nowhere else."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = Field(default="dev", alias="CRYPTOBOT_ENV")
    log_level: str = Field(default="INFO", alias="CRYPTOBOT_LOG_LEVEL")

    exchange_name: str = Field(default="binance", alias="EXCHANGE_NAME")
    exchange_api_key: str = Field(default="", alias="EXCHANGE_API_KEY")
    exchange_api_secret: str = Field(default="", alias="EXCHANGE_API_SECRET")
    exchange_testnet: bool = Field(default=True, alias="EXCHANGE_TESTNET")

    db_url: str = Field(
        default="sqlite:///./data/cryptobot.sqlite", alias="CRYPTOBOT_DB_URL"
    )
    data_dir: Path = Field(default=Path("./data"), alias="CRYPTOBOT_DATA_DIR")
    log_dir: Path = Field(default=Path("./logs"), alias="CRYPTOBOT_LOG_DIR")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    kill_switch_file: Path = Field(
        default=Path("./KILL_SWITCH"), alias="CRYPTOBOT_KILL_SWITCH_FILE"
    )


# --- YAML-driven settings (non-secret, strategy/run shape) -------------------


class RiskConfig(BaseModel):
    max_position_pct: float = 0.10       # max position as fraction of equity
    max_gross_exposure_pct: float = 0.50
    max_daily_loss_pct: float = 0.02
    max_orders_per_minute: int = 10
    require_stop_loss: bool = True
    symbol_allow_list: list[str] = Field(default_factory=list)


class StrategyConfig(BaseModel):
    name: str = "sma_crossover"
    params: dict[str, Any] = Field(default_factory=dict)


class MarketConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    timeframe: str = "1h"


class FeesConfig(BaseModel):
    taker_bps: float = 10.0     # 10 bps = 0.10%
    maker_bps: float = 5.0
    slippage_bps: float = 5.0


class RunConfig(BaseModel):
    """Shape of a config/*.yaml file."""

    mode: str = "backtest"             # backtest | paper | live (live disabled)
    market: MarketConfig = MarketConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    fees: FeesConfig = FeesConfig()


# --- combined settings -------------------------------------------------------


class Settings(BaseModel):
    env: EnvSettings
    run: RunConfig


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a YAML mapping at top level.")
    return data


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load environment settings and (optionally) a YAML run config.

    If `config_path` is None, a default `RunConfig` is returned — useful for
    CLI commands that do not need a strategy config (e.g. `cryptobot version`).
    """
    env = EnvSettings()  # type: ignore[call-arg]
    if config_path is None:
        return Settings(env=env, run=RunConfig())
    run = RunConfig(**_load_yaml(Path(config_path)))
    return Settings(env=env, run=run)
