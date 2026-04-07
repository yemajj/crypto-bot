"""Backtest broker skeleton (Phase 3).

Deterministic fills over historical bars with fees + slippage + next-bar
execution. Phase 1 scaffold only.
"""

from __future__ import annotations

from cryptobot.core.types import Fill, Order, Position
from cryptobot.execution.broker_base import Broker
from cryptobot.execution.fees import FeeModel


class BacktestBroker(Broker):
    def __init__(self, starting_cash: float, fees: FeeModel) -> None:
        self._cash = starting_cash
        self._fees = fees
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []

    def submit(self, order: Order) -> Order:
        # TODO (Phase 3): resolve fill at next bar open +/- slippage,
        # deduct fees, update cash/positions, append Fill.
        raise NotImplementedError("BacktestBroker.submit will be implemented in Phase 3.")

    def cancel(self, order_id: str) -> None:
        # TODO (Phase 3).
        raise NotImplementedError

    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def equity(self) -> float:
        return self._cash

    def recent_fills(self) -> list[Fill]:
        return list(self._fills)
