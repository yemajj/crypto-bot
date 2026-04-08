"""Telegram notifier for key trading events.

Uses only stdlib (urllib) — no extra dependencies.
When bot_token or chat_id is empty, send() is a no-op (safe to call always).
Failures are logged and swallowed so a Telegram outage never halts the bot.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from cryptobot.monitoring.logging_setup import get_logger

log = get_logger(component="notify")

_TIMEOUT_S = 5
_API_BASE = "https://api.telegram.org"


class Notifier:
    """Send short text messages via the Telegram Bot API."""

    def __init__(self, bot_token: str = "", chat_id: str = "") -> None:
        self._bot_token = bot_token.strip()
        self._chat_id = chat_id.strip()

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    def send(self, message: str) -> None:
        """Send `message` to the configured Telegram chat.

        Always logs the message (at debug level when Telegram is disabled).
        Silently swallows network / API errors so the run loop is not affected.
        """
        if not self.enabled:
            log.debug("notify_disabled", message=message)
            return

        log.info("notify_send", message=message)
        url = f"{_API_BASE}/bot{self._bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": self._chat_id,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                if resp.status != 200:
                    log.warning("notify_non200", status=resp.status)
        except urllib.error.URLError as exc:
            log.warning("notify_failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.warning("notify_error", error=str(exc))
