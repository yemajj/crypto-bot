"""Paper broker skeleton (Phase 4).

Simulates fills against a live market feed with fees and slippage. Phase 1
only provides the class and interface compliance.
"""

from __future__ import annotations

from cryptobot.core.types import Fill, Order, Position
from cryptobot.execution.broker_base import Broker
from cryptobot.execution.fees import FeeModel


class PaperBroker(Broker):
    def __init__(self, starting_cash: float, fees: FeeModel) -> None:
        self._cash = starting_cash
        self._fees = fees
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []

    def submit(self, order: Order) -> Order:
        # TODO (Phase 4): fill at next-bar open with slippage, update cash,
        # positions, and emit a Fill. Write to the journal.
        raise NotImplementedError("PaperBroker.submit will be implemented in Phase 4.")

    def cancel(self, order_id: str) -> None:
        # TODO (Phase 4).
        raise NotImplementedError

    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def equity(self) -> float:
        # TODO (Phase 4): mark positions to market.
        return self._cash

    def recent_fills(self) -> list[Fill]:
        return list(self._fills)
