"""Exchange client abstraction.

In v1 this is a read-only data interface. Order-placement methods will be
added in Phase 6 (and gated behind configuration + risk manager).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from cryptobot.core.types import Bar


class ExchangeClient(ABC):
    """Minimal read-only exchange interface."""

    name: str

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[Bar]:
        """Return OHLCV bars newest-last. Times must be UTC."""
        raise NotImplementedError

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> dict:
        """Return the current ticker snapshot (raw)."""
        raise NotImplementedError

    # TODO (Phase 6): create_order, cancel_order, fetch_balance,
    # fetch_open_orders — added only when live trading is enabled and
    # after LiveBroker + RiskManager gating is in place.
