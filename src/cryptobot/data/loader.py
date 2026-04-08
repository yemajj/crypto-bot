"""Historical OHLCV loader — paginated fetch from the exchange into BarStore.

Usage (CLI):
    cryptobot fetch-history --symbol BTC/USDT --timeframe 1h --since 2024-01-01

Usage (programmatic):
    loader = HistoricalLoader(client, bar_store)
    n = loader.fetch("BTC/USDT", "1h", since=datetime(2024, 1, 1, tzinfo=timezone.utc))

Design:
- Pages forward using the `since` parameter on each batch call.
- Batch size is 500 (safe upper bound for most exchanges; Binance allows 1 000).
- Writes each batch to BarStore immediately so progress is not lost on error.
- Intra-batch gap errors (BarGapError from CcxtClient) are logged as warnings
  and the loader skips forward by one batch window rather than halting — exchange
  historical data occasionally has holes and a collection run should be resumable.
- Stops when the exchange returns no bars, fewer bars than the limit (last page),
  or the last bar reaches/exceeds `until`.
- Idempotent: BarStore deduplicates on ts_open, so re-running over an overlapping
  range is safe and only fills in missing bars.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptobot.core.types import Bar, BarGapError
from cryptobot.data.storage import BarStore
from cryptobot.exchanges.base import ExchangeClient
from cryptobot.exchanges.ccxt_client import timeframe_to_seconds
from cryptobot.monitoring.logging_setup import get_logger

log = get_logger(component="loader")

_BATCH_SIZE = 500


class HistoricalLoader:
    """Fetches historical bars from an exchange and writes them to a BarStore."""

    def __init__(
        self,
        client: ExchangeClient,
        bar_store: BarStore,
        batch_size: int = _BATCH_SIZE,
    ) -> None:
        self._client = client
        self._bar_store = bar_store
        self._batch_size = batch_size

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        since: datetime,
        until: datetime | None = None,
    ) -> int:
        """Fetch bars in the range [since, until) and persist them to BarStore.

        Args:
            symbol:    Trading pair, e.g. "BTC/USDT".
            timeframe: Bar duration string, e.g. "1h", "4h", "1d".
            since:     Start of the range (UTC, inclusive).
            until:     End of the range (UTC, exclusive). Defaults to now.

        Returns:
            Total number of new bars written (across all batches).
        """
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        if until is not None and until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)

        bar_td = timedelta(seconds=timeframe_to_seconds(timeframe))
        cursor = since
        total_written = 0

        log.info(
            "historical_fetch_start",
            symbol=symbol,
            timeframe=timeframe,
            since=since.isoformat(),
            until=until.isoformat() if until else "now",
        )

        while True:
            if until is not None and cursor >= until:
                break

            try:
                batch = self._client.fetch_ohlcv(
                    symbol, timeframe, since=cursor, limit=self._batch_size
                )
            except BarGapError as exc:
                # Exchange data gap within this batch window — log, skip forward.
                log.warning(
                    "historical_gap_skipped",
                    symbol=symbol,
                    cursor=cursor.isoformat(),
                    error=str(exc),
                )
                cursor += bar_td * _BATCH_SIZE
                continue
            except Exception as exc:
                log.error("historical_fetch_error", symbol=symbol, error=str(exc))
                raise

            if not batch:
                log.info("historical_fetch_exhausted", symbol=symbol, cursor=cursor.isoformat())
                break

            # Trim to `until` if specified.
            if until is not None:
                batch = [b for b in batch if b.ts_open < until]
                if not batch:
                    break

            self._bar_store.write(symbol, timeframe, batch)
            total_written += len(batch)
            cursor = batch[-1].ts_open + bar_td

            log.info(
                "historical_batch_written",
                symbol=symbol,
                n=len(batch),
                up_to=batch[-1].ts_open.strftime("%Y-%m-%d %H:%M"),
                total=total_written,
            )

            # Fewer bars than limit means this is the last page.
            if len(batch) < self._batch_size:
                break

        log.info(
            "historical_fetch_done",
            symbol=symbol,
            timeframe=timeframe,
            total_bars=total_written,
        )
        return total_written
