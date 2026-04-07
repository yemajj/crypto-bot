"""Stable ID generation for runs, orders, and related entities.

Every execution of the bot gets a `run_id`. Everything downstream
(signals, orders, fills, log lines) is tagged with it so the journal
is fully reconstructable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_run_id(prefix: str = "run") -> str:
    """Return a human-scannable run id like `run-20260407T153012Z-ab12cd34`."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{prefix}-{ts}-{short}"


def new_order_id() -> str:
    """Return a unique client order id."""
    return f"ord-{uuid.uuid4().hex[:16]}"
