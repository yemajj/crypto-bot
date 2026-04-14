"""Market regime enum — defined in core so it can be imported without
triggering the strategy package's __init__ (which would cause circular imports
when allocation.allocator needs Regime but strategy.__init__ imports ensemble
which imports allocation.allocator).
"""

from __future__ import annotations

from enum import Enum


class Regime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    BREAKOUT_WATCH = "breakout_watch"
