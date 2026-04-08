"""CCXT-based exchange client (read-only in v1).

Wraps a single ccxt exchange instance. The ccxt import is deferred so the
package can be imported and tested without a network dependency.

All public OHLCV endpoints are unauthenticated; API keys are only needed for
order placement (Phase 6+, intentionally not implemented here).

Assumptions:
- CCXT returns OHLCV rows as [ts_ms, open, high, low, close, volume].
- Timestamps from CCXT are in UTC milliseconds.
- The last row in a fetch may be the currently-open (incomplete) bar.
  We drop any bar whose close time has not yet passed.
- Decimal precision: we convert floats via str() to avoid IEEE 754 artifacts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from cryptobot.core.types import Bar, BarGapError
from cryptobot.exchanges.base import ExchangeClient
from cryptobot.monitoring.logging_setup import get_logger

log = get_logger(component="ccxt_client")

_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
    "1w": 604800,
}


def timeframe_to_seconds(timeframe: str) -> int:
    """Convert a timeframe string like '1h' to seconds.

    Raises ValueError for unrecognised strings.
    """
    seconds = _TIMEFRAME_SECONDS.get(timeframe)
    if seconds is None:
        raise ValueError(
            f"Unrecognised timeframe {timeframe!r}. "
            f"Supported: {sorted(_TIMEFRAME_SECONDS)}"
        )
    return seconds


class CcxtClient(ExchangeClient):
    """Thin wrapper over a ccxt exchange instance."""

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
            raise ValueError(f"Unknown ccxt exchange: {self.name!r}")
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
        """Fetch up to `limit` closed OHLCV bars ending at the most recent
        closed bar.

        Args:
            symbol:    Trading pair in slash notation, e.g. "BTC/USDT".
            timeframe: Bar duration string, e.g. "1h", "4h", "1d".
            since:     Fetch bars on or after this UTC datetime (optional).
            limit:     Max bars to return per request (exchange-dependent cap).

        Returns:
            List of Bar objects sorted by ts_open ascending. The currently-open
            (incomplete) bar is always excluded.
        """
        self._ensure_client()
        bar_duration = timeframe_to_seconds(timeframe)
        since_ms = int(since.timestamp() * 1000) if since else None

        try:
            raw = self._client.fetch_ohlcv(  # type: ignore[union-attr]
                symbol, timeframe, since=since_ms, limit=limit
            )
        except Exception as exc:
            log.warning("ccxt_fetch_ohlcv_error", exchange=self.name, symbol=symbol, error=str(exc))
            raise

        now_utc = datetime.now(timezone.utc)
        bars: list[Bar] = []
        for row in raw:
            ts_ms, o, h, l, c, vol = row
            ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            # Drop bar if its close time has not yet passed (currently open).
            bar_close_time = ts + timedelta(seconds=bar_duration)
            if bar_close_time > now_utc:
                continue
            bars.append(
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    ts_open=ts,
                    open=Decimal(str(o)),
                    high=Decimal(str(h)),
                    low=Decimal(str(l)),
                    close=Decimal(str(c)),
                    volume=Decimal(str(vol)),
                )
            )

        # Verify consecutive bars are exactly one timeframe apart.
        bar_td = timedelta(seconds=bar_duration)
        for i in range(1, len(bars)):
            expected = bars[i - 1].ts_open + bar_td
            if bars[i].ts_open != expected:
                raise BarGapError(
                    f"Bar gap in {symbol} {timeframe}: "
                    f"expected {expected.isoformat()} after "
                    f"{bars[i - 1].ts_open.isoformat()}, "
                    f"got {bars[i].ts_open.isoformat()}"
                )

        return bars

    def fetch_ticker(self, symbol: str) -> dict:
        """Fetch the current best-bid/ask ticker for a symbol."""
        self._ensure_client()
        try:
            return self._client.fetch_ticker(symbol)  # type: ignore[union-attr]
        except Exception as exc:
            log.warning("ccxt_fetch_ticker_error", exchange=self.name, symbol=symbol, error=str(exc))
            raise
