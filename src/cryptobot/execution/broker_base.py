"""Broker interface shared by backtest, paper, and (later) live."""

from __future__ import annotations

from abc import ABC, abstractmethod

from cryptobot.core.types import Fill, Order, Position


class Broker(ABC):
    """Every broker — backtest, paper, live — implements this."""

    @abstractmethod
    def submit(self, order: Order) -> Order:
        """Submit an order. Returns the order with updated status."""
        raise NotImplementedError

    @abstractmethod
    def cancel(self, order_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def positions(self) -> dict[str, Position]:
        raise NotImplementedError

    @abstractmethod
    def equity(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def recent_fills(self) -> list[Fill]:
        raise NotImplementedError
