"""Live trading entry point.

Intentionally refuses to run in v1. Live trading is gated to Phase 6 and
requires: (a) tiny notional caps in config, (b) a stable paper-trading
history, (c) an explicit env flag to proceed.
"""

from __future__ import annotations

from pathlib import Path


class LiveTradingDisabled(RuntimeError):
    pass


def main(config_path: str | Path) -> str:  # pragma: no cover - guardrail
    raise LiveTradingDisabled(
        "Live trading is disabled in v1. "
        "Complete Phases 2–5 and validate paper trading first."
    )
