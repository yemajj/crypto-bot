"""Tests for backtest/param_grid.py — ParamGrid and _override_strategy_params."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cryptobot.backtest.metrics import Metrics
from cryptobot.backtest.param_grid import ParamGrid, _override_strategy_params

_SYMBOL = "BTC/USDT"
_TF = "1h"
_TS0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers shared with test_walk_forward.py pattern
# ---------------------------------------------------------------------------

def _make_settings(tmp_path: Path):
    from cryptobot.config.settings import (
        EnvSettings,
        FeesConfig,
        MarketConfig,
        RiskConfig,
        RunConfig,
        Settings,
        StrategyConfig,
    )

    env = EnvSettings(
        cryptobot_env="dev",
        exchange_name="binanceus",
        db_url="sqlite:///:memory:",
        data_dir=str(tmp_path / "data"),
        log_dir=str(tmp_path / "logs"),
        kill_switch_file=str(tmp_path / "KILL_SWITCH"),
    )
    run = RunConfig(
        mode="paper",
        market=MarketConfig(symbols=[_SYMBOL], timeframe=_TF),
        strategy=StrategyConfig(name="sma_crossover", params={"fast": 3, "slow": 6}),
        risk=RiskConfig(
            symbol_allow_list=[_SYMBOL],
            max_orders_per_minute=10,
            require_stop_loss=False,
            max_daily_loss_pct=0.99,
            max_position_pct=1.0,
            max_gross_exposure_pct=1.0,
        ),
        fees=FeesConfig(taker_bps=10.0, maker_bps=5.0, slippage_bps=5.0),
        starting_cash=10_000.0,
        warmup_bars=1,
        poll_interval_seconds=60.0,
    )
    return Settings(env=env, run=run)


def _fake_result(sharpe: float):
    """Build a minimal BacktestResult-like mock with the given Sharpe."""
    m = MagicMock()
    m.metrics = Metrics(sharpe=sharpe)
    return m


# ---------------------------------------------------------------------------
# ParamGrid.combinations()
# ---------------------------------------------------------------------------

def test_combinations_single_param():
    grid = ParamGrid({"fast": [10, 15, 20]})
    combos = grid.combinations()
    assert combos == [{"fast": 10}, {"fast": 15}, {"fast": 20}]


def test_combinations_cross_product():
    grid = ParamGrid({"fast": [10, 15], "slow": [40, 50]})
    combos = grid.combinations()
    assert len(combos) == 4
    assert {"fast": 10, "slow": 40} in combos
    assert {"fast": 10, "slow": 50} in combos
    assert {"fast": 15, "slow": 40} in combos
    assert {"fast": 15, "slow": 50} in combos


def test_combinations_three_params():
    grid = ParamGrid({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
    combos = grid.combinations()
    assert len(combos) == 8  # 2 × 2 × 2


def test_combinations_single_value():
    grid = ParamGrid({"fast": [10]})
    assert grid.combinations() == [{"fast": 10}]


def test_empty_grid_raises():
    with pytest.raises(ValueError, match="at least one parameter"):
        ParamGrid({})


# ---------------------------------------------------------------------------
# ParamGrid.best_params()
# ---------------------------------------------------------------------------

def test_best_params_picks_highest_sharpe(tmp_path):
    settings = _make_settings(tmp_path)
    grid = ParamGrid({"fast": [10, 15, 20]})

    _sharpe_by_fast = {10: 0.5, 15: 1.2, 20: 0.8}

    def fake_run(s, bars, run_id):
        fast = s.run.strategy.params["fast"]
        return _fake_result(_sharpe_by_fast[fast])

    best, best_sharpe = grid.best_params(settings, bars=[], run_fold_fn=fake_run)
    assert best == {"fast": 15}
    assert best_sharpe == pytest.approx(1.2)


def test_best_params_all_zero_sharpe_returns_first(tmp_path):
    settings = _make_settings(tmp_path)
    grid = ParamGrid({"fast": [10, 15]})

    def fake_run(s, bars, run_id):
        return _fake_result(0.0)

    best, best_sharpe = grid.best_params(settings, bars=[], run_fold_fn=fake_run)
    assert best == {"fast": 10}
    assert best_sharpe == pytest.approx(0.0)


def test_best_params_negative_sharpes_picks_least_negative(tmp_path):
    settings = _make_settings(tmp_path)
    grid = ParamGrid({"fast": [10, 15]})

    def fake_run(s, bars, run_id):
        fast = s.run.strategy.params["fast"]
        return _fake_result({10: -2.0, 15: -0.5}[fast])

    best, _ = grid.best_params(settings, bars=[], run_fold_fn=fake_run)
    assert best == {"fast": 15}


def test_best_params_calls_run_fold_with_overridden_settings(tmp_path):
    settings = _make_settings(tmp_path)
    grid = ParamGrid({"fast": [99]})
    seen_params = []

    def fake_run(s, bars, run_id):
        seen_params.append(s.run.strategy.params.copy())
        return _fake_result(1.0)

    grid.best_params(settings, bars=[], run_fold_fn=fake_run)
    assert len(seen_params) == 1
    assert seen_params[0]["fast"] == 99


# ---------------------------------------------------------------------------
# _override_strategy_params()
# ---------------------------------------------------------------------------

def test_override_merges_into_existing_params(tmp_path):
    settings = _make_settings(tmp_path)
    # Original has fast=3, slow=6
    new_settings = _override_strategy_params(settings, {"fast": 20})
    assert new_settings.run.strategy.params["fast"] == 20
    assert new_settings.run.strategy.params["slow"] == 6  # unchanged


def test_override_does_not_mutate_original(tmp_path):
    settings = _make_settings(tmp_path)
    _override_strategy_params(settings, {"fast": 99})
    assert settings.run.strategy.params["fast"] == 3  # original unchanged


def test_override_adds_new_key(tmp_path):
    settings = _make_settings(tmp_path)
    new_settings = _override_strategy_params(settings, {"atr_window": 14})
    assert new_settings.run.strategy.params["atr_window"] == 14
    # Original keys still present
    assert new_settings.run.strategy.params["fast"] == 3


def test_override_preserves_other_run_config_fields(tmp_path):
    settings = _make_settings(tmp_path)
    new_settings = _override_strategy_params(settings, {"fast": 20})
    assert new_settings.run.starting_cash == settings.run.starting_cash
    assert new_settings.env.kill_switch_file == settings.env.kill_switch_file
