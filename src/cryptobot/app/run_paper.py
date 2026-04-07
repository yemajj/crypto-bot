"""Entry point for paper trading. Skeleton — Phase 4 wires it in full."""

from __future__ import annotations

from pathlib import Path

from cryptobot.config import load_settings
from cryptobot.core.ids import new_run_id
from cryptobot.monitoring.logging_setup import setup_logging


def main(config_path: str | Path) -> str:
    settings = load_settings(config_path)
    run_id = new_run_id("paper")
    log = setup_logging(
        log_dir=settings.env.log_dir,
        level=settings.env.log_level,
        run_id=run_id,
    )
    log.info(
        "paper_start",
        config=str(config_path),
        strategy=settings.run.strategy.name,
        symbols=settings.run.market.symbols,
    )
    # TODO (Phase 4): build feed, PaperBroker, strategy, risk manager,
    # journal; run the loop; honor the kill switch.
    log.warning("paper_not_implemented", phase="Phase 4")
    return run_id
