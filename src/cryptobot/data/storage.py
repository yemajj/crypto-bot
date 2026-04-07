"""Persistence for OHLCV bars.

SQLite is the authoritative relational store (via the journal module);
parquet is used here as a bulk cache of raw bars per (symbol, timeframe).
Phase 1 scaffold only.
"""

from __future__ import annotations

from pathlib import Path

from cryptobot.core.types import Bar


class BarStore:
    """File-backed bar storage. Phase 2 will flesh this out."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: str) -> Path:
        safe = symbol.replace("/", "_")
        return self._data_dir / f"{safe}_{timeframe}.parquet"

    def write(self, symbol: str, timeframe: str, bars: list[Bar]) -> None:
        # TODO (Phase 2): convert to DataFrame, dedupe on ts_open,
        # merge with existing parquet file, write atomically.
        raise NotImplementedError("BarStore.write will be implemented in Phase 2.")

    def read(self, symbol: str, timeframe: str) -> list[Bar]:
        # TODO (Phase 2): read parquet and return Bar objects.
        raise NotImplementedError("BarStore.read will be implemented in Phase 2.")
