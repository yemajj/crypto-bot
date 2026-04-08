"""Parquet-backed bar cache for OHLCV data.

Parquet is used as a local cache to avoid re-fetching historical bars on every
paper-trading restart. The SQLite journal is the authoritative relational store
for runs/orders/fills; this cache is separate and purely for market data.

Design:
- One file per (symbol, timeframe): e.g. data/BTC_USDT_1h.parquet
- Bars are stored as float64 (exchange data is inherently float; Decimal is
  applied when converting back to Bar objects).
- Writes are atomic: written to a .tmp file first, then renamed.
- Deduplication and sorting by ts_open happen on every write so the file stays
  clean even if bars overlap across two writes.
"""

from __future__ import annotations

import os
from datetime import timezone
from decimal import Decimal
from pathlib import Path

from cryptobot.core.types import Bar
from cryptobot.monitoring.logging_setup import get_logger

log = get_logger(component="bar_store")


class BarStore:
    """Parquet-backed cache for OHLCV bars."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: str) -> Path:
        safe = symbol.replace("/", "_")
        return self._data_dir / f"{safe}_{timeframe}.parquet"

    def write(self, symbol: str, timeframe: str, bars: list[Bar]) -> None:
        """Merge `bars` into the cache for (symbol, timeframe).

        Deduplicates on ts_open and sorts ascending. Atomic: writes to a temp
        file and renames to the target path.
        """
        if not bars:
            return

        import pandas as pd

        new_df = _bars_to_df(bars)
        path = self._path(symbol, timeframe)

        if path.exists():
            try:
                existing = pd.read_parquet(path)
                combined = (
                    pd.concat([existing, new_df], ignore_index=True)
                    .drop_duplicates(subset="ts_open")
                    .sort_values("ts_open")
                    .reset_index(drop=True)
                )
            except Exception as exc:
                log.warning("bar_store_read_error_on_write", path=str(path), error=str(exc))
                combined = new_df.drop_duplicates(subset="ts_open").sort_values("ts_open")
        else:
            combined = new_df.drop_duplicates(subset="ts_open").sort_values("ts_open")

        tmp = path.with_suffix(".tmp.parquet")
        try:
            combined.to_parquet(tmp, index=False)
            os.replace(tmp, path)   # atomic on both Windows and Unix
        except Exception as exc:
            log.error("bar_store_write_failed", path=str(path), error=str(exc))
            tmp.unlink(missing_ok=True)
            raise

        log.debug("bar_store_wrote", symbol=symbol, timeframe=timeframe, n_total=len(combined))

    def read(self, symbol: str, timeframe: str) -> list[Bar]:
        """Read cached bars for (symbol, timeframe), sorted by ts_open ascending.

        Returns an empty list if no cache exists.
        """
        path = self._path(symbol, timeframe)
        if not path.exists():
            return []

        import pandas as pd

        try:
            df = pd.read_parquet(path).sort_values("ts_open").reset_index(drop=True)
        except Exception as exc:
            log.warning("bar_store_read_error", path=str(path), error=str(exc))
            return []

        bars: list[Bar] = []
        for _, row in df.iterrows():
            ts = row["ts_open"]
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            bars.append(Bar(
                symbol=symbol,
                timeframe=timeframe,
                ts_open=ts,
                open=Decimal(str(float(row["open"]))),
                high=Decimal(str(float(row["high"]))),
                low=Decimal(str(float(row["low"]))),
                close=Decimal(str(float(row["close"]))),
                volume=Decimal(str(float(row["volume"]))),
            ))

        log.debug("bar_store_read", symbol=symbol, timeframe=timeframe, n=len(bars))
        return bars

    def newest_ts(self, symbol: str, timeframe: str):
        """Return the ts_open of the most recent cached bar, or None."""
        bars = self.read(symbol, timeframe)
        return bars[-1].ts_open if bars else None


def _bars_to_df(bars: list[Bar]):
    import pandas as pd

    return pd.DataFrame({
        "ts_open": pd.to_datetime(
            [b.ts_open for b in bars], utc=True
        ),
        "open":   [float(b.open)   for b in bars],
        "high":   [float(b.high)   for b in bars],
        "low":    [float(b.low)    for b in bars],
        "close":  [float(b.close)  for b in bars],
        "volume": [float(b.volume) for b in bars],
    })
