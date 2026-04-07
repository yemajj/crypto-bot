"""Live market data feed for paper (and eventually live) trading.

The feed has two phases:
1. Warm-up: fetches the last `warmup_bars` closed bars immediately on startup
   and yields them so the strategy can build its indicator history before
   live signal generation begins.
2. Polling: sleeps for `poll_interval_seconds` between polls. On each poll,
   fetches the most recent few bars and yields any that have not been seen
   before (deduplication by ts_open).

Assumptions:
- All bars yielded are closed (the open bar is filtered out by CcxtClient).
- The `seen` set is in-memory; if the process restarts, warm-up bars will be
  re-yielded and re-processed by the run loop. This is acceptable for v1
  because broker state is also in-memory.
- A network error during polling is logged and retried on the next poll.
  A network error during warm-up propagates immediately (fail fast at startup).
- Rate limiting is handled by CCXT (`enableRateLimit=True` in CcxtClient).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime

from cryptobot.core.types import Bar
from cryptobot.exchanges.base import ExchangeClient
from cryptobot.monitoring.logging_setup import get_logger

log = get_logger(component="feed")

# Number of recent bars to fetch on each poll. 3 is enough for any timeframe
# at a 60-second poll interval (at most 1 new bar per poll for 1h+ timeframes).
_POLL_FETCH_LIMIT = 3


class MarketDataFeed:
    """Polls a single exchange client and yields new closed bars as they appear."""

    def __init__(
        self,
        client: ExchangeClient,
        symbols: list[str],
        timeframe: str,
        warmup_bars: int = 200,
        poll_interval_seconds: float = 60.0,
    ) -> None:
        self._client = client
        self._symbols = symbols
        self._timeframe = timeframe
        self._warmup_bars = warmup_bars
        self._poll_interval = poll_interval_seconds

    def stream(self) -> Iterator[Bar]:
        """Yield bars indefinitely: warm-up first, then live polling.

        The generator never returns; the caller must break out of the loop
        (e.g. via kill switch or KeyboardInterrupt).
        """
        seen: dict[str, set[datetime]] = {s: set() for s in self._symbols}

        # --- Phase 1: warm-up ------------------------------------------------
        for symbol in self._symbols:
            log.info(
                "feed_warmup_start",
                symbol=symbol,
                timeframe=self._timeframe,
                bars=self._warmup_bars,
            )
            initial = self._client.fetch_ohlcv(
                symbol, self._timeframe, limit=self._warmup_bars
            )
            for bar in initial:
                seen[symbol].add(bar.ts_open)
                yield bar
            log.info("feed_warmup_done", symbol=symbol, n_bars=len(initial))

        # --- Phase 2: live polling --------------------------------------------
        log.info("feed_polling_started", interval_s=self._poll_interval)
        while True:
            time.sleep(self._poll_interval)

            for symbol in self._symbols:
                try:
                    recent = self._client.fetch_ohlcv(
                        symbol, self._timeframe, limit=_POLL_FETCH_LIMIT
                    )
                except Exception as exc:
                    log.warning(
                        "feed_poll_error",
                        symbol=symbol,
                        error=str(exc),
                    )
                    continue

                for bar in recent:
                    if bar.ts_open not in seen[symbol]:
                        seen[symbol].add(bar.ts_open)
                        log.info(
                            "feed_new_bar",
                            symbol=symbol,
                            ts=bar.ts_open.isoformat(),
                            close=float(bar.close),
                        )
                        yield bar
