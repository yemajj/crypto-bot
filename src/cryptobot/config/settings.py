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
from pydantic import BaseModel, Field, field_validator, model_validator
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
    # --- paper-trading safeguards ---
    max_open_positions: int = 1          # max number of simultaneously held symbols
    cooldown_after_losses: int = 0       # trigger cooldown after N consecutive losses (0 = off)
    cooldown_bars: int = 0               # bars to pause entries after cooldown triggers

    @field_validator("max_position_pct", "max_gross_exposure_pct", "max_daily_loss_pct")
    @classmethod
    def must_be_positive_fraction(cls, v: float, info: object) -> float:
        if not (0 < v <= 1):
            raise ValueError(f"{info.field_name} must be in (0, 1], got {v}")  # type: ignore[union-attr]
        return v

    @field_validator("max_orders_per_minute")
    @classmethod
    def must_be_positive_int(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_orders_per_minute must be >= 1, got {v}")
        return v

    @field_validator("max_open_positions")
    @classmethod
    def must_be_positive_positions(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"max_open_positions must be >= 1, got {v}")
        return v

    @field_validator("cooldown_after_losses", "cooldown_bars")
    @classmethod
    def must_be_non_negative_int(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"cooldown fields must be >= 0, got {v}")
        return v

    @model_validator(mode="after")
    def cooldown_consistency(self) -> "RiskConfig":
        if self.cooldown_after_losses > 0 and self.cooldown_bars == 0:
            raise ValueError(
                "cooldown_after_losses is set but cooldown_bars is 0 — "
                "set cooldown_bars to the number of bars to pause after a loss streak"
            )
        return self


class StrategyConfig(BaseModel):
    name: str = "sma_crossover"
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_sma_params(self) -> "StrategyConfig":
        if self.name == "sma_crossover" and self.params:
            fast = self.params.get("fast")
            slow = self.params.get("slow")
            atr_window = self.params.get("atr_window")
            risk_pct = self.params.get("risk_per_trade_pct")
            if fast is not None and (not isinstance(fast, int) or fast < 2):
                raise ValueError(f"sma_crossover fast must be an int >= 2, got {fast}")
            if slow is not None and (not isinstance(slow, int) or slow < 2):
                raise ValueError(f"sma_crossover slow must be an int >= 2, got {slow}")
            if fast is not None and slow is not None and fast >= slow:
                raise ValueError(f"sma_crossover fast ({fast}) must be < slow ({slow})")
            if atr_window is not None and (not isinstance(atr_window, int) or atr_window < 2):
                raise ValueError(f"sma_crossover atr_window must be an int >= 2, got {atr_window}")
            if risk_pct is not None and not (0 < risk_pct <= 0.1):
                raise ValueError(f"sma_crossover risk_per_trade_pct must be in (0, 0.1], got {risk_pct}")
            notional_pct = self.params.get("max_position_notional_pct")
            if notional_pct is not None and not (0 < notional_pct <= 1):
                raise ValueError(
                    f"sma_crossover max_position_notional_pct must be in (0, 1], got {notional_pct}"
                )
        return self


class MarketConfig(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    timeframe: str = "1h"


class FeesConfig(BaseModel):
    taker_bps: float = 10.0     # 10 bps = 0.10%
    maker_bps: float = 5.0
    slippage_bps: float = 5.0

    @field_validator("taker_bps", "maker_bps", "slippage_bps")
    @classmethod
    def must_be_non_negative(cls, v: float, info: object) -> float:
        if v < 0:
            raise ValueError(f"{info.field_name} must be >= 0, got {v}")  # type: ignore[union-attr]
        return v


class RunConfig(BaseModel):
    """Shape of a config/*.yaml file."""

    mode: str = "backtest"             # backtest | paper | live (live disabled)
    market: MarketConfig = MarketConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    fees: FeesConfig = FeesConfig()
    starting_cash: float = 10_000.0    # initial simulated capital
    warmup_bars: int = 200             # historical bars to pre-load before live signals
    poll_interval_seconds: float = 60.0  # feed polling interval (paper mode)

    @field_validator("starting_cash")
    @classmethod
    def must_be_positive_cash(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"starting_cash must be > 0, got {v}")
        return v

    @field_validator("warmup_bars")
    @classmethod
    def must_be_positive_warmup(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"warmup_bars must be >= 1, got {v}")
        return v


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
