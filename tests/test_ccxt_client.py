"""Unit tests for CcxtClient.fetch_ohlcv gap detection.

No real network calls — we mock the inner ccxt client to return controlled
OHLCV rows and verify the gap-detection logic.

Bars are set in 2024 so they are always "closed" relative to the current
system clock; no datetime mocking is needed for those tests.
The one exception is the "drop open bar" test which uses a recent timestamp.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from cryptobot.core.types import Bar, BarGapError
from cryptobot.exchanges.ccxt_client import CcxtClient, timeframe_to_seconds

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SYMBOL = "BTC/USDT"
_TIMEFRAME = "1h"
_TS0 = datetime(2024, 1, 1, tzinfo=timezone.utc)   # well in the past


def _ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _row(dt: datetime, close: float = 100.0) -> list:
    """Build a CCXT OHLCV row for a fully-closed bar."""
    return [_ts_ms(dt), 99.0, 101.0, 98.0, close, 10.0]


def _make_client() -> CcxtClient:
    client = CcxtClient(name="binance")
    client._client = MagicMock()   # bypass _ensure_client()
    return client


# ---------------------------------------------------------------------------
# timeframe_to_seconds
# ---------------------------------------------------------------------------

def test_timeframe_to_seconds_known_values():
    assert timeframe_to_seconds("1m") == 60
    assert timeframe_to_seconds("1h") == 3600
    assert timeframe_to_seconds("4h") == 14400
    assert timeframe_to_seconds("1d") == 86400


def test_timeframe_to_seconds_unknown_raises():
    with pytest.raises(ValueError, match="Unrecognised timeframe"):
        timeframe_to_seconds("99x")


# ---------------------------------------------------------------------------
# fetch_ohlcv — happy path
# ---------------------------------------------------------------------------

def test_fetch_ohlcv_returns_contiguous_bars():
    """Three contiguous 1h bars are returned without error."""
    client = _make_client()
    client._client.fetch_ohlcv.return_value = [
        _row(_TS0),
        _row(_TS0 + timedelta(hours=1)),
        _row(_TS0 + timedelta(hours=2)),
    ]
    bars = client.fetch_ohlcv(_SYMBOL, _TIMEFRAME)
    assert len(bars) == 3
    assert all(isinstance(b, Bar) for b in bars)
    assert bars[0].ts_open == _TS0
    assert bars[2].ts_open == _TS0 + timedelta(hours=2)


def test_fetch_ohlcv_empty_returns_empty():
    client = _make_client()
    client._client.fetch_ohlcv.return_value = []
    assert client.fetch_ohlcv(_SYMBOL, _TIMEFRAME) == []


def test_fetch_ohlcv_single_bar_no_gap_check():
    """A single bar never triggers the gap check."""
    client = _make_client()
    client._client.fetch_ohlcv.return_value = [_row(_TS0)]
    bars = client.fetch_ohlcv(_SYMBOL, _TIMEFRAME)
    assert len(bars) == 1


def test_fetch_ohlcv_drops_currently_open_bar():
    """A bar that is still open (close time > now) is excluded."""
    client = _make_client()
    now = datetime.now(timezone.utc)
    # Bar opened 30 min ago closes 30 min from now → still open.
    open_bar_ts = now - timedelta(minutes=30)
    client._client.fetch_ohlcv.return_value = [
        _row(_TS0),                                   # safely closed (2024)
        [_ts_ms(open_bar_ts), 99.0, 101.0, 98.0, 100.0, 10.0],
    ]
    bars = client.fetch_ohlcv(_SYMBOL, _TIMEFRAME)
    assert len(bars) == 1
    assert bars[0].ts_open == _TS0


# ---------------------------------------------------------------------------
# fetch_ohlcv — gap detection
# ---------------------------------------------------------------------------

def test_fetch_ohlcv_raises_on_missing_middle_bar():
    """Hour 2 is missing between hour 1 and hour 3 → BarGapError."""
    client = _make_client()
    client._client.fetch_ohlcv.return_value = [
        _row(_TS0),
        _row(_TS0 + timedelta(hours=1)),
        _row(_TS0 + timedelta(hours=3)),   # hour 2 missing
    ]
    with pytest.raises(BarGapError, match="Bar gap"):
        client.fetch_ohlcv(_SYMBOL, _TIMEFRAME)


def test_fetch_ohlcv_raises_on_first_pair_gap():
    """Gap at the very first pair of bars is detected."""
    client = _make_client()
    client._client.fetch_ohlcv.return_value = [
        _row(_TS0),
        _row(_TS0 + timedelta(hours=2)),   # hour 1 missing
    ]
    with pytest.raises(BarGapError):
        client.fetch_ohlcv(_SYMBOL, _TIMEFRAME)


def test_fetch_ohlcv_gap_error_message_includes_timestamps():
    """Error message identifies the expected and actual timestamps."""
    client = _make_client()
    client._client.fetch_ohlcv.return_value = [
        _row(_TS0),
        _row(_TS0 + timedelta(hours=3)),
    ]
    with pytest.raises(BarGapError) as exc_info:
        client.fetch_ohlcv(_SYMBOL, _TIMEFRAME)
    msg = str(exc_info.value)
    assert "BTC/USDT" in msg
    assert "1h" in msg
