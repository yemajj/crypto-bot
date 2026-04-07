"""Individual risk rules.

Each rule is small and independently testable. The RiskManager runs them
in order and short-circuits on the first rejection. Rules never mutate
state; they return a verdict.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from cryptobot.core.types import Intent


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""

    @classmethod
    def allow(cls) -> Verdict:
        return cls(True, "")

    @classmethod
    def deny(cls, reason: str) -> Verdict:
        return cls(False, reason)


@dataclass
class RiskState:
    """Mutable runtime state the rules need to read.

    Kept as a simple dataclass so that tests can construct arbitrary scenarios
    without spinning up a full RiskManager.
    """

    equity: float
    gross_exposure: float
    daily_pnl: float
    orders_this_minute: int
    open_intents_by_symbol: dict[str, int]


class RiskRule(ABC):
    name: str = "rule"

    @abstractmethod
    def check(self, intent: Intent, state: RiskState) -> Verdict:
        raise NotImplementedError


class SymbolAllowList(RiskRule):
    name = "symbol_allow_list"

    def __init__(self, allowed: list[str]) -> None:
        self._allowed = set(allowed)

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if not self._allowed:
            return Verdict.allow()
        if intent.symbol not in self._allowed:
            return Verdict.deny(f"symbol {intent.symbol} not in allow-list")
        return Verdict.allow()


class MaxOrdersPerMinute(RiskRule):
    name = "max_orders_per_minute"

    def __init__(self, limit: int) -> None:
        self._limit = limit

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if state.orders_this_minute >= self._limit:
            return Verdict.deny(
                f"orders/min limit hit ({state.orders_this_minute}>={self._limit})"
            )
        return Verdict.allow()


class OneOrderPerSymbolInFlight(RiskRule):
    name = "one_order_per_symbol_in_flight"

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if state.open_intents_by_symbol.get(intent.symbol, 0) >= 1:
            return Verdict.deny(f"order already in flight for {intent.symbol}")
        return Verdict.allow()


class RequireStopLoss(RiskRule):
    name = "require_stop_loss"

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if intent.stop_price is None:
            return Verdict.deny("stop-loss required but missing")
        return Verdict.allow()


class MaxDailyLoss(RiskRule):
    name = "max_daily_loss"

    def __init__(self, max_loss_pct: float) -> None:
        self._max_loss_pct = max_loss_pct

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if state.equity <= 0:
            return Verdict.deny("non-positive equity")
        pnl_pct = state.daily_pnl / state.equity
        if pnl_pct <= -abs(self._max_loss_pct):
            return Verdict.deny(
                f"daily loss {pnl_pct:.4f} exceeds cap {self._max_loss_pct:.4f}"
            )
        return Verdict.allow()


class KillSwitchFile(RiskRule):
    name = "kill_switch_file"

    def __init__(self, path: Path) -> None:
        self._path = path

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if self._path.exists():
            return Verdict.deny(f"kill switch file present: {self._path}")
        return Verdict.allow()
