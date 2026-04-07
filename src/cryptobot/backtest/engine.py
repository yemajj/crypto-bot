"""Bar-by-bar backtest engine (skeleton).

Phase 3 will implement: load history -> iterate bars -> build context ->
call strategy -> run risk rules -> submit to BacktestBroker -> write to
journal -> compute metrics.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptobot.execution.backtest_broker import BacktestBroker
from cryptobot.risk.manager import RiskManager
from cryptobot.strategy.base import Strategy


@dataclass
class BacktestResult:
    run_id: str
    n_bars: int
    final_equity: float
    # TODO (Phase 3): metrics, trades, equity curve.


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        broker: BacktestBroker,
        risk: RiskManager,
    ) -> None:
        self._strategy = strategy
        self._broker = broker
        self._risk = risk

    def run(self, run_id: str) -> BacktestResult:
        # TODO (Phase 3): walk bars, call strategy.on_bar, pass intents
        # through the risk manager, submit approved orders to the broker,
        # persist journal rows, emit metrics.
        raise NotImplementedError("BacktestEngine.run will be implemented in Phase 3.")
