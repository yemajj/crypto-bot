"""Live market data feed (REST polling in v1).

Phase 1 scaffold only — the real poller arrives in Phase 2.
"""

from __future__ import annotations

from collections.abc import Iterator

from cryptobot.core.types import Bar
from cryptobot.exchanges.base import ExchangeClient


class MarketDataFeed:
    """Iterates over fresh bars from an exchange.

    Phase 1: skeleton. Phase 2: REST polling with gap detection and dedupe.
    """

    def __init__(
        self,
        client: ExchangeClient,
        symbols: list[str],
        timeframe: str,
    ) -> None:
        self._client = client
        self._symbols = symbols
        self._timeframe = timeframe

    def stream(self) -> Iterator[Bar]:
        # TODO (Phase 2): poll on a schedule, deduplicate, and yield new
        # bars as they close. Respect exchange rate limits.
        raise NotImplementedError("MarketDataFeed.stream will be implemented in Phase 2.")
