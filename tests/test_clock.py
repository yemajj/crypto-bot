from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cryptobot.core.clock import Clock, SimClock


def test_clock_now_is_utc_aware():
    ts = Clock().now()
    assert ts.tzinfo is not None
    assert ts.utcoffset() == timedelta(0)


def test_simclock_requires_tz_aware_start():
    with pytest.raises(ValueError):
        SimClock(datetime(2026, 1, 1))  # naive


def test_simclock_advances_forward_only():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    clock = SimClock(start)
    assert clock.now() == start
    clock.advance_to(start + timedelta(hours=1))
    assert clock.now() == start + timedelta(hours=1)
    with pytest.raises(ValueError):
        clock.advance_to(start)  # backwards
