"""CCXT-based exchange client (read-only in v1).

Phase 1 scaffold: this module defines the class and wiring, but methods are
stubs. Phase 2 will implement `fetch_ohlcv` against a real exchange.
"""

from __future__ import annotations

from datetime import datetime

from cryptobot.core.types import Bar
from cryptobot.exchanges.base import ExchangeClient
from cryptobot.monitoring.logging_setup import get_logger

log = get_logger(component="ccxt_client")


class CcxtClient(ExchangeClient):
    """Thin wrapper over a ccxt exchange instance.

    The ccxt import is deferred so that the package can be imported (and
    tested) without hitting any network dependency during Phase 1.
    """

    def __init__(
        self,
        name: str,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
    ) -> None:
        self.name = name
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._client = None  # lazy; built in _ensure_client()

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        import ccxt  # deferred import

        if not hasattr(ccxt, self.name):
            raise ValueError(f"Unknown ccxt exchange: {self.name}")
        klass = getattr(ccxt, self.name)
        self._client = klass(
            {
                "apiKey": self._api_key or None,
                "secret": self._api_secret or None,
                "enableRateLimit": True,
            }
        )
        if self._testnet and hasattr(self._client, "set_sandbox_mode"):
            self._client.set_sandbox_mode(True)
        log.info("ccxt_client_ready", exchange=self.name, testnet=self._testnet)

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[Bar]:
        # TODO (Phase 2): implement against self._client.fetch_ohlcv and map
        # raw rows to Bar objects. Enforce UTC, dedupe, detect gaps.
        raise NotImplementedError("fetch_ohlcv will be implemented in Phase 2.")

    def fetch_ticker(self, symbol: str) -> dict:
        # TODO (Phase 2): call self._client.fetch_ticker(symbol).
        raise NotImplementedError("fetch_ticker will be implemented in Phase 2.")
