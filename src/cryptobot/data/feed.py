"""Live market data feed for paper (and eventually live) trading.

The feed has two phases:
1. Warm-up: fetches the last `warmup_bars` closed bars immediately on startup
   and yields them so the strategy can build its indicator history before
   live signal generation begins.
2. Polling: sleeps for `poll_interval_seconds` between polls. On each poll,
   fetches the most recent few bars and yields any that have not been seen
   before (deduplication by ts_open).

When a BarStore is provided the feed caches bars locally in parquet files,
so restarts only fetch the bars that are missing from the cache — typically
a handful rather than the full warmup window. The BarStore is optional;
omitting it restores the original behaviour of fetching everything from the
exchange on every startup.

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
from datetime import datetime, timedelta

from cryptobot.core.types import Bar, BarGapError
from cryptobot.exchanges.base import ExchangeClient
from cryptobot.exchanges.ccxt_client import timeframe_to_seconds
from cryptobot.monitoring.logging_setup import get_logger

log = get_logger(component="feed")

# Number of recent bars to fetch on each poll. 3 is enough for any timeframe
# at a 60-second poll interval (at most 1 new bar per poll for 1h+ timeframes).
_POLL_FETCH_LIMIT = 3

# Max bars to fetch in a single gap-fill request when the cache is stale.
_GAP_FETCH_LIMIT = 500


class MarketDataFeed:
    """Polls a single exchange client and yields new closed bars as they appear."""

    def __init__(
        self,
        client: ExchangeClient,
        symbols: list[str],
        timeframe: str,
        warmup_bars: int = 200,
        poll_interval_seconds: float = 60.0,
        bar_store=None,  # BarStore | None — avoids circular import at type level
    ) -> None:
        self._client = client
        self._symbols = symbols
        self._timeframe = timeframe
        self._warmup_bars = warmup_bars
        self._poll_interval = poll_interval_seconds
        self._bar_store = bar_store

    def stream(self) -> Iterator[Bar]:
        """Yield bars indefinitely: warm-up first, then live polling.

        The generator never returns; the caller must break out of the loop
        (e.g. via kill switch or KeyboardInterrupt).
        """
        seen: dict[str, set[datetime]] = {s: set() for s in self._symbols}
        last_ts: dict[str, datetime | None] = {s: None for s in self._symbols}
        bar_td = timedelta(seconds=timeframe_to_seconds(self._timeframe))

        # --- Phase 1: warm-up ------------------------------------------------
        for symbol in self._symbols:
            log.info(
                "feed_warmup_start",
                symbol=symbol,
                timeframe=self._timeframe,
                bars=self._warmup_bars,
                cached=self._bar_store is not None,
            )
            initial = self._warmup_bars_for(symbol)
            for bar in initial:
                seen[symbol].add(bar.ts_open)
                last_ts[symbol] = bar.ts_open
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
                except BarGapError:
                    raise  # intra-batch gap — halt the feed
                except Exception as exc:
                    log.warning(
                        "feed_poll_error",
                        symbol=symbol,
                        error=str(exc),
                    )
                    continue

                new_bars: list[Bar] = []
                for bar in recent:
                    if bar.ts_open not in seen[symbol]:
                        # Cross-batch contiguity check.
                        prev = last_ts[symbol]
                        if prev is not None:
                            expected = prev + bar_td
                            if bar.ts_open != expected:
                                raise BarGapError(
                                    f"Bar gap in {symbol} {self._timeframe}: "
                                    f"expected {expected.isoformat()} after "
                                    f"{prev.isoformat()}, "
                                    f"got {bar.ts_open.isoformat()}"
                                )
                        seen[symbol].add(bar.ts_open)
                        last_ts[symbol] = bar.ts_open
                        new_bars.append(bar)
                        log.info(
                            "feed_new_bar",
                            symbol=symbol,
                            ts=bar.ts_open.isoformat(),
                            close=float(bar.close),
                        )
                        yield bar

                if new_bars and self._bar_store is not None:
                    try:
                        self._bar_store.write(symbol, self._timeframe, new_bars)
                    except Exception as exc:
                        log.warning("bar_store_poll_write_error", error=str(exc))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _warmup_bars_for(self, symbol: str) -> list[Bar]:
        """Return `warmup_bars` bars for a symbol, using the cache when possible.

        Without cache: fetches `warmup_bars` from the exchange (original behaviour).
        With cache:
          1. Read cached bars.
          2. Fetch only the gap (bars since last cached ts) from the exchange.
          3. Merge gap bars into the cache.
          4. Return the last `warmup_bars` from the combined set.
        """
        if self._bar_store is None:
            return self._client.fetch_ohlcv(
                symbol, self._timeframe, limit=self._warmup_bars
            )

        bar_td = timedelta(seconds=timeframe_to_seconds(self._timeframe))
        cached = self._bar_store.read(symbol, self._timeframe)

        if not cached:
            # Cold start: fetch and prime the cache.
            fetched = self._client.fetch_ohlcv(
                symbol, self._timeframe, limit=self._warmup_bars
            )
            if fetched:
                try:
                    self._bar_store.write(symbol, self._timeframe, fetched)
                except Exception as exc:
                    log.warning("bar_store_warmup_write_error", error=str(exc))
            log.info("feed_cache_cold_start", symbol=symbol, n_fetched=len(fetched))
            return fetched

        # Warm start: fetch only the gap since the last cached bar.
        last_cached_ts = cached[-1].ts_open
        since = last_cached_ts + bar_td
        try:
            gap_bars = self._client.fetch_ohlcv(
                symbol, self._timeframe, since=since, limit=_GAP_FETCH_LIMIT
            )
        except Exception as exc:
            # Network error during gap fill — fall back to the cache as-is.
            log.warning("bar_store_gap_fill_error", symbol=symbol, error=str(exc))
            gap_bars = []

        if gap_bars:
            try:
                self._bar_store.write(symbol, self._timeframe, gap_bars)
                cached = self._bar_store.read(symbol, self._timeframe)
            except Exception as exc:
                log.warning("bar_store_gap_write_error", error=str(exc))
                cached = cached + gap_bars

        log.info(
            "feed_cache_warm_start",
            symbol=symbol,
            cached=len(cached),
            gap_fetched=len(gap_bars),
        )
        return cached[-self._warmup_bars:] if len(cached) > self._warmup_bars else cached
