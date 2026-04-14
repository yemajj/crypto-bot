"""Individual risk rules.

Each rule is small and independently testable. The RiskManager runs them
in order and short-circuits on the first rejection. Rules never mutate
state; they return a verdict.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from cryptobot.core.types import Intent, Side


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

    `consecutive_losses` is incremented by the run loop after each losing fill
    and reset to 0 after `cooldown_bars` bars have elapsed. CooldownAfterLoss
    reads it; the run loop writes it.

    `mark_price_by_symbol` maps symbol → current mark price (float). The run
    loop populates this before calling risk.evaluate so that position-size
    rules can compute notional values without accessing broker state.
    """

    equity: float
    gross_exposure: float
    daily_pnl: float
    orders_this_minute: int
    open_intents_by_symbol: dict[str, int]
    consecutive_losses: int = 0
    mark_price_by_symbol: dict[str, float] = field(default_factory=dict)


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
        if intent.side == Side.SELL:
            return Verdict.allow()  # exits must never be blocked
        if state.open_intents_by_symbol.get(intent.symbol, 0) >= 1:
            return Verdict.deny(f"order already in flight for {intent.symbol}")
        return Verdict.allow()


class RequireStopLoss(RiskRule):
    name = "require_stop_loss"

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if intent.side == Side.SELL:
            return Verdict.allow()  # closing a position needs no stop
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


class MaxOpenPositions(RiskRule):
    """Deny new entries when the number of open positions is at the cap.

    `state.open_intents_by_symbol` must be populated by the run loop as
    `{symbol: 1}` for every symbol with qty > 0.
    Exits (SELL) are always allowed regardless of the cap.
    """

    name = "max_open_positions"

    def __init__(self, max_positions: int) -> None:
        self._max = max_positions

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if intent.side == Side.SELL:
            return Verdict.allow()
        # Adding to an already-open position doesn't open a new one.
        if intent.symbol in state.open_intents_by_symbol:
            return Verdict.allow()
        n_open = len(state.open_intents_by_symbol)
        if n_open >= self._max:
            return Verdict.deny(
                f"max open positions reached ({n_open} >= {self._max})"
            )
        return Verdict.allow()


class CooldownAfterLoss(RiskRule):
    """Deny new entries while a loss streak cooldown is active.

    The run loop increments `state.consecutive_losses` after each losing fill
    and resets it to 0 after `cooldown_bars` bars have elapsed. This rule only
    reads the counter — it does NOT manage timing.

    Exits (SELL) are always allowed so that an open position can be closed
    even during a cooldown period.
    """

    name = "cooldown_after_loss"

    def __init__(self, consecutive_losses_threshold: int) -> None:
        self._threshold = consecutive_losses_threshold

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if intent.side == Side.SELL:
            return Verdict.allow()
        if state.consecutive_losses >= self._threshold:
            return Verdict.deny(
                f"cooldown active: {state.consecutive_losses} consecutive losses "
                f">= threshold {self._threshold}"
            )
        return Verdict.allow()


class MaxPositionSizePct(RiskRule):
    """Deny a BUY intent whose notional value exceeds a fraction of equity.

    notional = intent.qty * mark_price_by_symbol[symbol].
    If the symbol has no mark price in state, the intent is denied (cannot
    validate).
    Exits (SELL) are always allowed.
    """

    name = "max_position_size_pct"

    def __init__(self, max_pct: float) -> None:
        self._max_pct = max_pct

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if intent.side == Side.SELL:
            return Verdict.allow()
        price = state.mark_price_by_symbol.get(intent.symbol)
        if price is None:
            return Verdict.deny(
                f"no mark price for {intent.symbol} — cannot validate position size"
            )
        if state.equity <= 0:
            return Verdict.deny("non-positive equity")
        notional = float(intent.qty) * price
        ratio = notional / state.equity
        if ratio > self._max_pct:
            return Verdict.deny(
                f"position size {ratio:.4f} exceeds cap {self._max_pct:.4f}"
            )
        return Verdict.allow()


class MaxGrossExposurePct(RiskRule):
    """Deny a BUY intent that would push total gross exposure over a cap.

    projected_exposure = current gross_exposure + intent notional.
    Exits (SELL) are always allowed.
    """

    name = "max_gross_exposure_pct"

    def __init__(self, max_pct: float) -> None:
        self._max_pct = max_pct

    def check(self, intent: Intent, state: RiskState) -> Verdict:
        if intent.side == Side.SELL:
            return Verdict.allow()
        price = state.mark_price_by_symbol.get(intent.symbol)
        if price is None:
            return Verdict.deny(
                f"no mark price for {intent.symbol} — cannot validate gross exposure"
            )
        if state.equity <= 0:
            return Verdict.deny("non-positive equity")
        notional = float(intent.qty) * price
        projected = (state.gross_exposure + notional) / state.equity
        if projected > self._max_pct:
            return Verdict.deny(
                f"projected gross exposure {projected:.4f} exceeds cap {self._max_pct:.4f}"
            )
        return Verdict.allow()
