"""Unit tests for BarStore: write/read round-trip, deduplication, atomic write."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.core.types import Bar
from cryptobot.data.storage import BarStore

_SYMBOL = "BTC/USDT"
_TF = "1h"
_TS0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bar(idx: int, close: float = 100.0) -> Bar:
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_TS0 + timedelta(hours=idx),
        open=Decimal(str(close)),
        high=Decimal(str(close * 1.01)),
        low=Decimal(str(close * 0.99)),
        close=Decimal(str(close)),
        volume=Decimal("1.5"),
    )


def test_read_empty_when_no_file(tmp_path):
    store = BarStore(tmp_path)
    assert store.read(_SYMBOL, _TF) == []


def test_write_then_read_round_trip(tmp_path):
    store = BarStore(tmp_path)
    bars = [_bar(0), _bar(1), _bar(2)]
    store.write(_SYMBOL, _TF, bars)
    result = store.read(_SYMBOL, _TF)
    assert len(result) == 3
    assert result[0].ts_open == _TS0
    assert result[-1].ts_open == _TS0 + timedelta(hours=2)
    # Decimal fields survive the round-trip.
    assert result[1].close == Decimal("100.0")


def test_write_deduplicates_on_ts_open(tmp_path):
    store = BarStore(tmp_path)
    store.write(_SYMBOL, _TF, [_bar(0), _bar(1)])
    # Second write overlaps bar 1, adds bar 2.
    store.write(_SYMBOL, _TF, [_bar(1, close=999.0), _bar(2)])
    result = store.read(_SYMBOL, _TF)
    assert len(result) == 3
    # bar 1's close should be the original value (first-write wins via dedup on ts_open).
    assert result[1].close == Decimal("100.0")


def test_write_sorts_ascending(tmp_path):
    store = BarStore(tmp_path)
    store.write(_SYMBOL, _TF, [_bar(2), _bar(0), _bar(1)])
    result = store.read(_SYMBOL, _TF)
    assert [b.ts_open for b in result] == [
        _TS0 + timedelta(hours=i) for i in range(3)
    ]


def test_incremental_write_appends(tmp_path):
    store = BarStore(tmp_path)
    store.write(_SYMBOL, _TF, [_bar(0), _bar(1)])
    store.write(_SYMBOL, _TF, [_bar(2), _bar(3)])
    result = store.read(_SYMBOL, _TF)
    assert len(result) == 4


def test_newest_ts(tmp_path):
    store = BarStore(tmp_path)
    assert store.newest_ts(_SYMBOL, _TF) is None
    store.write(_SYMBOL, _TF, [_bar(0), _bar(1), _bar(2)])
    assert store.newest_ts(_SYMBOL, _TF) == _TS0 + timedelta(hours=2)


def test_different_symbols_are_independent(tmp_path):
    store = BarStore(tmp_path)
    eth_bar = Bar(
        symbol="ETH/USDT",
        timeframe=_TF,
        ts_open=_TS0,
        open=Decimal("200"),
        high=Decimal("202"),
        low=Decimal("198"),
        close=Decimal("200"),
        volume=Decimal("5"),
    )
    store.write(_SYMBOL, _TF, [_bar(0)])
    store.write("ETH/USDT", _TF, [eth_bar])
    btc = store.read(_SYMBOL, _TF)
    eth = store.read("ETH/USDT", _TF)
    assert len(btc) == 1
    assert len(eth) == 1
    assert btc[0].symbol == _SYMBOL
    assert eth[0].symbol == "ETH/USDT"


def test_write_empty_list_is_noop(tmp_path):
    store = BarStore(tmp_path)
    store.write(_SYMBOL, _TF, [])
    assert store.read(_SYMBOL, _TF) == []
