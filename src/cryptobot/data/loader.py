"""Historical OHLCV loader.

Phase 1 scaffold only. Phase 2 will implement paginated fetches from the
exchange and bulk upserts into storage.
"""

from __future__ import annotations

from datetime import datetime

from cryptobot.core.types import Bar
from cryptobot.exchanges.base import ExchangeClient


class HistoricalLoader:
    def __init__(self, client: ExchangeClient) -> None:
        self._client = client

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        until: datetime | None = None,
    ) -> list[Bar]:
        # TODO (Phase 2): paginate via fetch_ohlcv, merge, dedupe, validate.
        raise NotImplementedError("HistoricalLoader.fetch will be implemented in Phase 2.")
