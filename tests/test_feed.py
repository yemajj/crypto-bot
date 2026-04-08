"""Unit tests for MarketDataFeed gap detection.

Uses a stub ExchangeClient so no CCXT/network dependency.

Because stream() is an infinite generator, tests that expect success use
_take_n() to pull a specific number of bars then close the generator.
Tests that expect BarGapError let the exception propagate naturally.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.core.types import Bar, BarGapError
from cryptobot.data.feed import MarketDataFeed
from cryptobot.exchanges.base import ExchangeClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SYMBOL = "BTC/USDT"
_TIMEFRAME = "1h"
_TS0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bar(idx: int) -> Bar:
    ts = _TS0 + timedelta(hours=idx)
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TIMEFRAME,
        ts_open=ts,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("10"),
    )


class _StubClient(ExchangeClient):
    """Returns pre-configured bar sequences.

    The first call to fetch_ohlcv is treated as the warm-up call;
    all subsequent calls are treated as polling calls.
    """

    def __init__(self, warmup_bars: list[Bar], poll_sequences: list[list[Bar]]):
        self._warmup = warmup_bars
        self._polls = iter(poll_sequences)
        self._warmup_called = False

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since=None,
        limit: int = 500,
    ) -> list[Bar]:
        if not self._warmup_called:
            self._warmup_called = True
            return self._warmup
        return next(self._polls, [])

    def fetch_ticker(self, symbol: str) -> dict:
        return {}


def _feed(client: ExchangeClient, warmup: int = 2) -> MarketDataFeed:
    return MarketDataFeed(
        client,
        symbols=[_SYMBOL],
        timeframe=_TIMEFRAME,
        warmup_bars=warmup,
        poll_interval_seconds=0.0,   # no sleep in tests
    )


def _take_n(feed: MarketDataFeed, n: int) -> list[Bar]:
    """Pull exactly n bars from the feed then close the generator."""
    gen = feed.stream()
    result: list[Bar] = []
    try:
        for bar in gen:
            result.append(bar)
            if len(result) >= n:
                break
    finally:
        gen.close()
    return result


# ---------------------------------------------------------------------------
# Warm-up tests
# ---------------------------------------------------------------------------

def test_warmup_yields_all_bars():
    """Warm-up bars are all yielded before any polling bars."""
    warmup = [_bar(0), _bar(1)]
    client = _StubClient(warmup_bars=warmup, poll_sequences=[])
    bars = _take_n(_feed(client), 2)
    assert len(bars) == 2
    assert bars[0].ts_open == _TS0
    assert bars[1].ts_open == _TS0 + timedelta(hours=1)


def test_warmup_gap_propagates():
    """BarGapError from fetch_ohlcv during warm-up propagates immediately."""
    class _GappedClient(ExchangeClient):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500):
            raise BarGapError("gap in warmup")
        def fetch_ticker(self, symbol): return {}

    with pytest.raises(BarGapError, match="gap in warmup"):
        _take_n(_feed(_GappedClient()), 1)


# ---------------------------------------------------------------------------
# Cross-batch gap detection (polling phase)
# ---------------------------------------------------------------------------

def test_polling_detects_cross_batch_gap():
    """A missing bar between warm-up and first poll raises BarGapError."""
    # Warm-up ends at hour 1 (last_ts = hour 1).
    # First poll returns hour 3, skipping hour 2 → gap.
    warmup = [_bar(0), _bar(1)]
    poll1 = [_bar(3)]   # hour 2 missing
    client = _StubClient(warmup_bars=warmup, poll_sequences=[poll1])

    with pytest.raises(BarGapError, match="Bar gap"):
        _take_n(_feed(client), 3)


def test_polling_no_gap_yields_contiguous_bar():
    """A contiguous new bar in a poll is yielded normally."""
    warmup = [_bar(0), _bar(1)]
    poll1 = [_bar(1), _bar(2)]   # bar 1 already seen; bar 2 is new and contiguous
    client = _StubClient(warmup_bars=warmup, poll_sequences=[poll1])
    bars = _take_n(_feed(client), 3)   # 2 warmup + 1 new
    assert len(bars) == 3
    assert bars[-1].ts_open == _TS0 + timedelta(hours=2)


def test_polling_deduplicates_seen_bars():
    """Bars already yielded in warm-up are not yielded again in polling."""
    warmup = [_bar(0), _bar(1)]
    poll1 = [_bar(0), _bar(1)]   # all already seen — nothing new
    poll2 = [_bar(2)]             # new bar
    client = _StubClient(warmup_bars=warmup, poll_sequences=[poll1, poll2])
    bars = _take_n(_feed(client), 3)
    assert len(bars) == 3
    # Bars 0 and 1 from warmup, bar 2 from second poll
    assert bars[0].ts_open == _TS0
    assert bars[2].ts_open == _TS0 + timedelta(hours=2)


def test_polling_intra_batch_gap_propagates():
    """BarGapError raised by fetch_ohlcv during a poll call propagates."""
    class _GappedPollClient(ExchangeClient):
        def __init__(self):
            self._warmup_called = False

        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500):
            if not self._warmup_called:
                self._warmup_called = True
                return [_bar(0), _bar(1)]
            raise BarGapError("intra-batch gap")

        def fetch_ticker(self, symbol): return {}

    with pytest.raises(BarGapError, match="intra-batch gap"):
        _take_n(_feed(_GappedPollClient()), 3)
