"""Tests for HistoricalLoader: pagination, gap handling, until boundary, idempotency."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.core.types import Bar, BarGapError
from cryptobot.data.loader import HistoricalLoader, _BATCH_SIZE
from cryptobot.data.storage import BarStore
from cryptobot.exchanges.base import ExchangeClient

_SYMBOL = "BTC/USDT"
_TF = "1h"
_TS0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bar(idx: int) -> Bar:
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_TS0 + timedelta(hours=idx),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )


class _StubClient(ExchangeClient):
    """Returns pre-loaded batches in sequence; empty list when exhausted."""

    def __init__(self, batches: list[list[Bar]]) -> None:
        self._batches = list(batches)

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500) -> list[Bar]:
        if not self._batches:
            return []
        return self._batches.pop(0)

    def fetch_ticker(self, symbol) -> dict:
        return {}


class _GapClient(ExchangeClient):
    """First call raises BarGapError, second returns bars normally."""

    def __init__(self, fallback: list[Bar]) -> None:
        self._called = False
        self._fallback = fallback

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=500) -> list[Bar]:
        if not self._called:
            self._called = True
            raise BarGapError("exchange gap")
        return self._fallback

    def fetch_ticker(self, symbol) -> dict:
        return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_single_page_written_to_store(tmp_path):
    bars = [_bar(i) for i in range(5)]
    client = _StubClient([bars])
    store = BarStore(tmp_path)
    loader = HistoricalLoader(client, store)
    n = loader.fetch(_SYMBOL, _TF, since=_TS0)
    assert n == 5
    assert len(store.read(_SYMBOL, _TF)) == 5


def test_multi_page_accumulates(tmp_path):
    # Two full pages of 3 bars each — loader should follow both.
    page1 = [_bar(i) for i in range(3)]
    page2 = [_bar(i) for i in range(3, 5)]   # partial last page → stops
    client = _StubClient([page1, page2])
    store = BarStore(tmp_path)
    loader = HistoricalLoader(client, store, batch_size=3)
    n = loader.fetch(_SYMBOL, _TF, since=_TS0)
    assert n == 5
    cached = store.read(_SYMBOL, _TF)
    assert len(cached) == 5
    assert cached[-1].ts_open == _TS0 + timedelta(hours=4)


def test_stops_on_empty_response(tmp_path):
    # First call returns bars; second returns empty → done.
    page1 = [_bar(i) for i in range(3)]
    client = _StubClient([page1, []])
    store = BarStore(tmp_path)
    loader = HistoricalLoader(client, store)
    n = loader.fetch(_SYMBOL, _TF, since=_TS0)
    assert n == 3


def test_until_trims_results(tmp_path):
    # 5 bars but until = hour 3 → only bars 0, 1, 2 should be written.
    bars = [_bar(i) for i in range(5)]
    client = _StubClient([bars])
    store = BarStore(tmp_path)
    loader = HistoricalLoader(client, store)
    until = _TS0 + timedelta(hours=3)
    n = loader.fetch(_SYMBOL, _TF, since=_TS0, until=until)
    assert n == 3
    cached = store.read(_SYMBOL, _TF)
    assert all(b.ts_open < until for b in cached)


def test_gap_error_is_skipped_and_fetch_continues(tmp_path):
    # First call raises BarGapError; second returns 2 bars → loader recovers.
    fallback = [_bar(600), _bar(601)]  # far-future bars (after gap skip)
    client = _GapClient(fallback)
    store = BarStore(tmp_path)
    loader = HistoricalLoader(client, store)
    n = loader.fetch(_SYMBOL, _TF, since=_TS0)
    # 0 from the gap call + 2 from the fallback, but the fallback is < BATCH_SIZE so stops.
    assert n == 2
    assert len(store.read(_SYMBOL, _TF)) == 2


def test_idempotent_refetch(tmp_path):
    # Running the same fetch twice should not double-count bars.
    bars = [_bar(i) for i in range(4)]
    store = BarStore(tmp_path)
    # First fetch.
    loader1 = HistoricalLoader(_StubClient([bars]), store)
    loader1.fetch(_SYMBOL, _TF, since=_TS0)
    # Second fetch with same bars.
    loader2 = HistoricalLoader(_StubClient([bars]), store)
    loader2.fetch(_SYMBOL, _TF, since=_TS0)
    assert len(store.read(_SYMBOL, _TF)) == 4  # no duplicates


def test_returns_zero_when_no_data(tmp_path):
    client = _StubClient([[]])
    store = BarStore(tmp_path)
    loader = HistoricalLoader(client, store)
    n = loader.fetch(_SYMBOL, _TF, since=_TS0)
    assert n == 0
