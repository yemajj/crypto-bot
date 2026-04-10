"""Walk-forward backtest validation.

Splits a bar series into N equal-width rolling folds. For each fold, runs a
fresh BacktestEngine on the in-sample window, then on the out-of-sample window,
and returns the paired results.

Two entry points:

``run_walk_forward`` — fixed params (no optimisation):
    Same strategy params for every fold, matching what you configured in YAML.

``run_optimised_walk_forward`` — per-fold parameter grid search:
    For each fold, runs all grid combinations on the in-sample window, selects
    the best Sharpe, then validates those params on the out-of-sample window.
    Returns ``OptimisedFoldResult`` which extends ``FoldResult`` with the chosen
    params and in-sample Sharpe.

Neither entry point writes to the journal — walk-forward is a research tool.

Typical usage:
    bars = load_bars_from_csv(path, symbol, timeframe)
    results = run_walk_forward(settings, bars, folds=5, in_sample_pct=0.7)
    print_walk_forward_report(results, symbol, timeframe)

    grid = ParamGrid({"fast": [10, 15, 20], "slow": [40, 50, 60]})
    opt_results = run_optimised_walk_forward(settings, bars, grid, folds=5)
    print_optimised_walk_forward_report(opt_results, symbol, timeframe)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptobot.backtest.engine import BacktestEngine, BacktestResult
from cryptobot.backtest.metrics import Metrics
from cryptobot.backtest.param_grid import ParamGrid
from cryptobot.config.settings import Settings
from cryptobot.core.ids import new_run_id
from cryptobot.core.types import Bar
from cryptobot.execution.backtest_broker import BacktestBroker
from cryptobot.execution.fees import FeeModel
from cryptobot.risk.manager import RiskManager
from cryptobot.risk.rules import (
    KillSwitchFile,
    MaxDailyLoss,
    MaxGrossExposurePct,
    MaxOrdersPerMinute,
    MaxPositionSizePct,
    OneOrderPerSymbolInFlight,
    RequireStopLoss,
    SymbolAllowList,
)
from cryptobot.strategy.registry import get_strategy


@dataclass
class FoldResult:
    fold: int                    # 1-indexed
    in_sample_start: datetime
    in_sample_end: datetime
    out_sample_start: datetime
    out_sample_end: datetime
    in_sample_bars: int
    out_sample_bars: int
    in_sample: BacktestResult
    out_sample: BacktestResult


def run_walk_forward(
    settings: Settings,
    bars: list[Bar],
    folds: int = 5,
    in_sample_pct: float = 0.7,
) -> list[FoldResult]:
    """Run a rolling walk-forward validation over *bars*.

    Parameters
    ----------
    settings:
        Fully loaded Settings (env + YAML). Strategy params, risk caps, and
        fees come from here. Starting cash is taken from settings.run.starting_cash.
    bars:
        Complete historical bar series, sorted ascending. Must contain at least
        ``folds * 10`` bars so each window has meaningful data.
    folds:
        Number of equally-sized rolling windows.
    in_sample_pct:
        Fraction of each window used for in-sample training (default 0.7 = 70%).

    Returns
    -------
    list[FoldResult]
        One FoldResult per fold, in chronological order.
    """
    if folds < 2:
        raise ValueError(f"folds must be >= 2, got {folds}")
    if not (0.1 <= in_sample_pct <= 0.9):
        raise ValueError(f"in_sample_pct must be between 0.1 and 0.9, got {in_sample_pct}")

    min_bars = folds * 10
    if len(bars) < min_bars:
        raise ValueError(
            f"Need at least {min_bars} bars for {folds} folds, got {len(bars)}."
            " Provide more historical data or reduce --folds."
        )

    window = len(bars) // folds
    in_size = max(2, int(window * in_sample_pct))
    out_size = window - in_size

    if out_size < 1:
        raise ValueError(
            f"Out-of-sample window is 0 bars (window={window}, in_sample_pct={in_sample_pct})."
            " Reduce --in-sample-pct or increase --folds."
        )

    results: list[FoldResult] = []
    for fold_idx in range(folds):
        start = fold_idx * window
        in_bars = bars[start : start + in_size]
        out_bars = bars[start + in_size : start + window]

        if len(in_bars) < 2 or len(out_bars) < 1:
            continue  # skip degenerate tail fold

        in_result = _run_fold(settings, in_bars, run_id=new_run_id("wf"))
        out_result = _run_fold(settings, out_bars, run_id=new_run_id("wf"))

        results.append(FoldResult(
            fold=fold_idx + 1,
            in_sample_start=in_bars[0].ts_open,
            in_sample_end=in_bars[-1].ts_open,
            out_sample_start=out_bars[0].ts_open,
            out_sample_end=out_bars[-1].ts_open,
            in_sample_bars=len(in_bars),
            out_sample_bars=len(out_bars),
            in_sample=in_result,
            out_sample=out_result,
        ))

    if not results:
        raise ValueError("No valid folds produced — bars list may be too short.")

    return results


@dataclass
class OptimisedFoldResult(FoldResult):
    """FoldResult extended with per-fold best params from grid search."""

    best_params: dict[str, Any] = None          # type: ignore[assignment]
    best_in_sample_sharpe: float = 0.0


def run_optimised_walk_forward(
    settings: Settings,
    bars: list[Bar],
    grid: ParamGrid,
    folds: int = 5,
    in_sample_pct: float = 0.7,
) -> list[OptimisedFoldResult]:
    """Walk-forward with per-fold parameter optimisation.

    For each fold:
      1. Run all grid combinations on the in-sample window.
      2. Select the combination with the highest in-sample Sharpe.
      3. Re-run those best params on the out-of-sample window.

    Parameters
    ----------
    settings:
        Base settings.  Strategy params are overridden per grid combination.
    bars:
        Complete historical bar series, sorted ascending.
    grid:
        ``ParamGrid`` defining which params to search over.
    folds, in_sample_pct:
        Same semantics as ``run_walk_forward``.
    """
    if folds < 2:
        raise ValueError(f"folds must be >= 2, got {folds}")
    if not (0.1 <= in_sample_pct <= 0.9):
        raise ValueError(f"in_sample_pct must be between 0.1 and 0.9, got {in_sample_pct}")

    min_bars = folds * 10
    if len(bars) < min_bars:
        raise ValueError(
            f"Need at least {min_bars} bars for {folds} folds, got {len(bars)}."
            " Provide more historical data or reduce --folds."
        )

    window = len(bars) // folds
    in_size = max(2, int(window * in_sample_pct))
    out_size = window - in_size

    if out_size < 1:
        raise ValueError(
            f"Out-of-sample window is 0 bars (window={window}, in_sample_pct={in_sample_pct})."
            " Reduce --in-sample-pct or increase --folds."
        )

    results: list[OptimisedFoldResult] = []
    for fold_idx in range(folds):
        start = fold_idx * window
        in_bars = bars[start : start + in_size]
        out_bars = bars[start + in_size : start + window]

        if len(in_bars) < 2 or len(out_bars) < 1:
            continue

        best_params, best_sharpe = grid.best_params(
            base_settings=settings,
            bars=in_bars,
            run_fold_fn=_run_fold,
        )

        # Re-run best params on in-sample for reporting, then on out-of-sample
        from cryptobot.backtest.param_grid import _override_strategy_params
        best_settings = _override_strategy_params(settings, best_params)
        in_result = _run_fold(best_settings, in_bars, run_id=new_run_id("wfopt"))
        out_result = _run_fold(best_settings, out_bars, run_id=new_run_id("wfopt"))

        results.append(OptimisedFoldResult(
            fold=fold_idx + 1,
            in_sample_start=in_bars[0].ts_open,
            in_sample_end=in_bars[-1].ts_open,
            out_sample_start=out_bars[0].ts_open,
            out_sample_end=out_bars[-1].ts_open,
            in_sample_bars=len(in_bars),
            out_sample_bars=len(out_bars),
            in_sample=in_result,
            out_sample=out_result,
            best_params=best_params,
            best_in_sample_sharpe=best_sharpe,
        ))

    if not results:
        raise ValueError("No valid folds produced — bars list may be too short.")

    return results


def print_optimised_walk_forward_report(
    results: list[OptimisedFoldResult],
    symbol: str,
    timeframe: str,
) -> None:
    """Print a formatted optimised walk-forward summary to stdout."""
    sep = "=" * 80
    thin = "-" * 80

    print(f"\n{sep}")
    print(f"  OPTIMISED WALK-FORWARD  {symbol}  {timeframe}  ({len(results)} folds)")
    print(sep)
    print(
        f"  {'Fold':>4}  {'Window':^23}  "
        f"{'Sharpe':>7}  {'MaxDD':>7}  {'WinRate':>8}  {'Trades':>6}  {'Return':>8}  {'Best params'}"
    )

    for r in results:
        params_str = " ".join(f"{k}={v}" for k, v in sorted(r.best_params.items()))
        _print_fold_row("IN ", r.fold, r.in_sample_start, r.in_sample_end, r.in_sample.metrics)
        _print_fold_row("OUT", r.fold, r.out_sample_start, r.out_sample_end, r.out_sample.metrics)
        print(f"  {'':>4}  {'best: ' + params_str:<23}")
        print(thin)

    out_metrics = [r.out_sample.metrics for r in results]
    mean_sharpe = _mean(m.sharpe for m in out_metrics)
    mean_dd = _mean(m.max_drawdown for m in out_metrics)
    mean_wr = _mean(m.hit_rate for m in out_metrics)
    total_trades = sum(m.n_trades for m in out_metrics)
    mean_ret = _mean(m.total_return for m in out_metrics)

    print(
        f"  {'':>4}  {'OUT-OF-SAMPLE MEAN':^23}  "
        f"  {mean_sharpe:>6.2f}  {-mean_dd * 100:>6.1f}%"
        f"  {mean_wr * 100:>7.1f}%  {total_trades:>6d}  {mean_ret * 100:>+7.1f}%"
    )
    print(f"{sep}\n")


def _run_fold(settings: Settings, bars: list[Bar], run_id: str) -> BacktestResult:
    """Assemble a fresh engine for a single fold and run it.

    Each call constructs independent strategy, broker, and risk manager instances
    so folds share no state.
    """
    rc = settings.run.risk
    fc = settings.run.fees
    sc = settings.run.strategy

    fees = FeeModel(
        taker_bps=fc.taker_bps,
        maker_bps=fc.maker_bps,
        slippage_bps=fc.slippage_bps,
    )
    starting_cash = getattr(settings.run, "starting_cash", 10_000.0)
    broker = BacktestBroker(starting_cash=starting_cash, fees=fees)

    rules = [
        SymbolAllowList(rc.symbol_allow_list),
        MaxOrdersPerMinute(rc.max_orders_per_minute),
        OneOrderPerSymbolInFlight(),
    ]
    if rc.require_stop_loss:
        rules.append(RequireStopLoss())
    rules += [
        MaxDailyLoss(max_loss_pct=rc.max_daily_loss_pct),
        MaxPositionSizePct(rc.max_position_pct),
        MaxGrossExposurePct(rc.max_gross_exposure_pct),
        KillSwitchFile(settings.env.kill_switch_file),
    ]
    risk = RiskManager(rules)

    strategy_cls = get_strategy(sc.name)
    strategy = strategy_cls(params=sc.params)

    engine = BacktestEngine(strategy=strategy, broker=broker, risk=risk, bars=bars)
    return engine.run(run_id)


def print_walk_forward_report(
    results: list[FoldResult],
    symbol: str,
    timeframe: str,
) -> None:
    """Print a formatted walk-forward summary to stdout."""
    sep = "=" * 72
    thin = "-" * 72

    print(f"\n{sep}")
    print(f"  WALK-FORWARD  {symbol}  {timeframe}  ({len(results)} folds)")
    print(sep)
    print(
        f"  {'Fold':>4}  {'Window':^23}  "
        f"{'Sharpe':>7}  {'MaxDD':>7}  {'WinRate':>8}  {'Trades':>6}  {'Return':>8}"
    )

    for r in results:
        _print_fold_row("IN ", r.fold, r.in_sample_start, r.in_sample_end, r.in_sample.metrics)
        _print_fold_row("OUT", r.fold, r.out_sample_start, r.out_sample_end, r.out_sample.metrics)
        print(thin)

    # Summary row: mean of out-of-sample metrics across folds.
    out_metrics = [r.out_sample.metrics for r in results]
    mean_sharpe = _mean(m.sharpe for m in out_metrics)
    mean_dd = _mean(m.max_drawdown for m in out_metrics)
    mean_wr = _mean(m.hit_rate for m in out_metrics)
    total_trades = sum(m.n_trades for m in out_metrics)
    mean_ret = _mean(m.total_return for m in out_metrics)

    print(
        f"  {'':>4}  {'OUT-OF-SAMPLE MEAN':^23}  "
        f"  {mean_sharpe:>6.2f}  {-mean_dd * 100:>6.1f}%"
        f"  {mean_wr * 100:>7.1f}%  {total_trades:>6d}  {mean_ret * 100:>+7.1f}%"
    )
    print(f"{sep}\n")


def _print_fold_row(
    label: str,
    fold: int,
    start: datetime,
    end: datetime,
    m: Metrics,
) -> None:
    window = f"{start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}"
    print(
        f"  {fold:>3}{label}  {window:<23}  "
        f"  {m.sharpe:>6.2f}  {-m.max_drawdown * 100:>6.1f}%"
        f"  {m.hit_rate * 100:>7.1f}%  {m.n_trades:>6d}  {m.total_return * 100:>+7.1f}%"
    )


def _mean(values) -> float:
    vals = list(values)
    return sum(vals) / len(vals) if vals else 0.0
