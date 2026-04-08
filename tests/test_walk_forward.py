"""Tests for the walk-forward backtest utility."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cryptobot.backtest.walk_forward import FoldResult, _mean, run_walk_forward
from cryptobot.core.types import Bar

_SYMBOL = "BTC/USDT"
_TF = "1h"
_TS0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(idx: int, close: float = 50_000.0) -> Bar:
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_TS0 + timedelta(hours=idx),
        open=Decimal(str(close)),
        high=Decimal(str(close * 1.01)),
        low=Decimal(str(close * 0.99)),
        close=Decimal(str(close)),
        volume=Decimal("10"),
    )


def _make_bars(n: int) -> list[Bar]:
    return [_bar(i) for i in range(n)]


def _make_settings(tmp_path: Path):
    """Build a minimal Settings object pointing at a temp kill-switch path."""
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


# ---------------------------------------------------------------------------
# Fold slicing
# ---------------------------------------------------------------------------

def test_fold_count(tmp_path):
    """run_walk_forward returns exactly the requested number of folds."""
    settings = _make_settings(tmp_path)
    bars = _make_bars(100)
    results = run_walk_forward(settings, bars, folds=5, in_sample_pct=0.7)
    assert len(results) == 5


def test_fold_indices_are_one_indexed(tmp_path):
    settings = _make_settings(tmp_path)
    bars = _make_bars(100)
    results = run_walk_forward(settings, bars, folds=5, in_sample_pct=0.7)
    assert [r.fold for r in results] == [1, 2, 3, 4, 5]


def test_fold_windows_are_non_overlapping(tmp_path):
    """Out-of-sample end of fold N must be <= in-sample start of fold N+1."""
    settings = _make_settings(tmp_path)
    bars = _make_bars(100)
    results = run_walk_forward(settings, bars, folds=5, in_sample_pct=0.7)
    for i in range(len(results) - 1):
        assert results[i].out_sample_end <= results[i + 1].in_sample_start


def test_in_sample_larger_than_out_sample(tmp_path):
    """With in_sample_pct=0.7, in-sample window must be larger than out-of-sample."""
    settings = _make_settings(tmp_path)
    bars = _make_bars(100)
    results = run_walk_forward(settings, bars, folds=5, in_sample_pct=0.7)
    for r in results:
        assert r.in_sample_bars > r.out_sample_bars


def test_bar_counts_sum_to_window(tmp_path):
    """in_sample_bars + out_sample_bars == window size for each fold."""
    settings = _make_settings(tmp_path)
    n = 100
    folds = 5
    bars = _make_bars(n)
    window = n // folds
    results = run_walk_forward(settings, bars, folds=folds, in_sample_pct=0.7)
    for r in results:
        assert r.in_sample_bars + r.out_sample_bars == window


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_too_few_bars_raises(tmp_path):
    settings = _make_settings(tmp_path)
    bars = _make_bars(5)  # way below min_bars = folds * 10 = 50
    with pytest.raises(ValueError, match="at least"):
        run_walk_forward(settings, bars, folds=5)


def test_folds_less_than_2_raises(tmp_path):
    settings = _make_settings(tmp_path)
    bars = _make_bars(100)
    with pytest.raises(ValueError, match="folds must be"):
        run_walk_forward(settings, bars, folds=1)


def test_invalid_in_sample_pct_raises(tmp_path):
    settings = _make_settings(tmp_path)
    bars = _make_bars(100)
    with pytest.raises(ValueError, match="in_sample_pct"):
        run_walk_forward(settings, bars, folds=5, in_sample_pct=0.99)


# ---------------------------------------------------------------------------
# Results structure
# ---------------------------------------------------------------------------

def test_each_fold_has_backtest_result(tmp_path):
    """Each FoldResult has a non-None in_sample and out_sample BacktestResult."""
    settings = _make_settings(tmp_path)
    bars = _make_bars(100)
    results = run_walk_forward(settings, bars, folds=5, in_sample_pct=0.7)
    for r in results:
        assert r.in_sample is not None
        assert r.out_sample is not None
        assert r.in_sample.n_bars > 0
        assert r.out_sample.n_bars > 0


def test_fold_results_have_equity_curves(tmp_path):
    settings = _make_settings(tmp_path)
    bars = _make_bars(100)
    results = run_walk_forward(settings, bars, folds=5, in_sample_pct=0.7)
    for r in results:
        assert len(r.in_sample.equity_curve) >= 2
        assert len(r.out_sample.equity_curve) >= 2


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def test_mean_empty():
    assert _mean([]) == 0.0


def test_mean_values():
    assert _mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)
