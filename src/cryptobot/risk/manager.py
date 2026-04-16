"""Risk manager: the gatekeeper between strategies and brokers.

Every Intent passes through `RiskManager.evaluate`. Only approved intents
become Orders. Rejections are logged with reasons.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptobot.core.types import Intent
from cryptobot.monitoring.logging_setup import get_logger
from cryptobot.risk.rules import RiskRule, RiskState, Verdict

log = get_logger(component="risk")


@dataclass
class Decision:
    intent: Intent
    verdict: Verdict


class RiskManager:
    def __init__(self, rules: list[RiskRule]) -> None:
        self._rules = rules

    def evaluate(self, intent: Intent, state: RiskState) -> Decision:
        for rule in self._rules:
            verdict = rule.check(intent, state)
            if not verdict.allowed:
                log.warning(
                    "risk_denied",
                    rule=rule.name,
                    symbol=intent.symbol,
                    reason=verdict.reason,
                )
                return Decision(intent=intent, verdict=verdict)
        log.debug("risk_allowed", symbol=intent.symbol, strategy=intent.strategy_id)
        return Decision(intent=intent, verdict=Verdict.allow())
