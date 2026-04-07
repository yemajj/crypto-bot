"""Time abstraction.

The bot runs in three modes — backtest (simulated time), paper (real time),
and (later) live (real time). Code that needs "now" should ask the Clock,
never `datetime.utcnow()` directly. This keeps backtests deterministic and
makes timezone bugs easier to catch.
"""

from __future__ import annotations

from datetime import datetime, timezone


class Clock:
    """Base clock. Always returns tz-aware UTC datetimes."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class SimClock(Clock):
    """Deterministic clock for backtests.

    The backtest engine is responsible for advancing this clock as it walks
    forward through bars.
    """

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise ValueError("SimClock requires a tz-aware start datetime (UTC).")
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance_to(self, ts: datetime) -> None:
        if ts.tzinfo is None:
            raise ValueError("advance_to requires a tz-aware datetime (UTC).")
        ts = ts.astimezone(timezone.utc)
        if ts < self._now:
            raise ValueError("SimClock cannot move backwards.")
        self._now = ts
