"""Strategy base class.

Strategies are **pure**: given a context (current bar + recent history +
current position), they return a list of Intents. Side effects (orders,
logging of fills, etc.) happen in the execution layer, never here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from cryptobot.core.types import Bar, Intent, Position


@dataclass
class StrategyContext:
    """What a strategy sees on each step.

    `history` is the recent bars for the symbol, oldest-first, ending with
    the most recently closed bar. `position` is the current net position for
    the symbol (may be flat).
    """

    symbol: str
    history: list[Bar]
    position: Position
    equity: float   # portfolio equity in quote currency
    params: dict[str, Any]


class Strategy(ABC):
    """Base class for all strategies."""

    #: Stable identifier used in logs and the journal.
    name: str = "base"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = params or {}

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        """Return zero or more Intents for this bar. Must be pure."""
        raise NotImplementedError
