"""Optional notifications (Telegram). Stub for Phase 5."""

from __future__ import annotations

from cryptobot.monitoring.logging_setup import get_logger

log = get_logger(component="notify")


class Notifier:
    """Minimal notifier interface.

    v1: logs the message. Phase 5 will add a Telegram implementation.
    """

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    def send(self, message: str) -> None:
        # TODO (Phase 5): push to Telegram Bot API when enabled.
        log.info("notify", enabled=self.enabled, message=message)
