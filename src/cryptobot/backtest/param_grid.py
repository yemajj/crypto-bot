"""Parameter grid search for walk-forward optimization.

Generates all combinations of parameter values and selects the best-performing
set on an in-sample window, then validates on an out-of-sample window.

Usage::

    grid = ParamGrid({"fast": [10, 15, 20], "slow": [40, 50, 60]})
    best_params, best_sharpe = grid.best_params(
        base_settings=settings,
        bars=in_sample_bars,
        run_fold_fn=_run_fold,
    )
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Callable

from cryptobot.backtest.engine import BacktestResult
from cryptobot.config.settings import Settings
from cryptobot.core.ids import new_run_id
from cryptobot.core.types import Bar


@dataclass
class GridCandidate:
    params: dict[str, Any]
    sharpe: float
    result: BacktestResult


class ParamGrid:
    """Exhaustive grid search over a dict of param_name → list[values].

    Example::

        grid = ParamGrid({"fast": [10, 15, 20], "slow": [40, 50, 60]})
    """

    def __init__(self, grid: dict[str, list[Any]]) -> None:
        if not grid:
            raise ValueError("grid must have at least one parameter")
        self._grid = grid

    def combinations(self) -> list[dict[str, Any]]:
        """Return all cross-product combinations as a list of dicts."""
        keys = list(self._grid.keys())
        values = list(self._grid.values())
        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    def best_params(
        self,
        base_settings: Settings,
        bars: list[Bar],
        run_fold_fn: Callable[[Settings, list[Bar], str], BacktestResult],
    ) -> tuple[dict[str, Any], float]:
        """Run all grid combinations on *bars* and return the best (params, sharpe).

        Parameters
        ----------
        base_settings:
            Base settings whose strategy params will be overridden per combination.
        bars:
            In-sample bar series to optimise against.
        run_fold_fn:
            Callable matching ``_run_fold(settings, bars, run_id) -> BacktestResult``.
            Injected so this module doesn't import walk_forward directly.

        Returns
        -------
        (best_params, best_sharpe)
            best_params is the overridden strategy params dict; best_sharpe is the
            in-sample Sharpe for that combination.  If all combinations produce
            Sharpe == 0 the first combination is returned.
        """
        combos = self.combinations()
        best: GridCandidate | None = None

        for combo in combos:
            candidate_settings = _override_strategy_params(base_settings, combo)
            result = run_fold_fn(candidate_settings, bars, new_run_id("grid"))
            sharpe = result.metrics.sharpe

            if best is None or sharpe > best.sharpe:
                best = GridCandidate(params=combo, sharpe=sharpe, result=result)

        if best is None:
            return combos[0], 0.0
        return best.params, best.sharpe


def _override_strategy_params(settings: Settings, overrides: dict[str, Any]) -> Settings:
    """Return a shallow copy of settings with strategy.params updated by *overrides*.

    Pydantic v2 models are immutable, so we use model_copy(update=...) with a
    reconstructed nested hierarchy.
    """
    run = settings.run
    sc = run.strategy

    # Merge overrides into existing strategy params (overrides win)
    merged_params = {**sc.params, **overrides}

    new_strategy = sc.model_copy(update={"params": merged_params})
    new_run = run.model_copy(update={"strategy": new_strategy})
    return settings.model_copy(update={"run": new_run})
