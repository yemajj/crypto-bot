"""Entry point for backtest runs. Skeleton — Phase 3 wires it in full."""

from __future__ import annotations

from pathlib import Path

from cryptobot.config import load_settings
from cryptobot.core.ids import new_run_id
from cryptobot.monitoring.logging_setup import setup_logging


def main(config_path: str | Path) -> str:
    settings = load_settings(config_path)
    run_id = new_run_id("bt")
    log = setup_logging(
        log_dir=settings.env.log_dir,
        level=settings.env.log_level,
        run_id=run_id,
    )
    log.info(
        "backtest_start",
        config=str(config_path),
        strategy=settings.run.strategy.name,
        symbols=settings.run.market.symbols,
    )
    # TODO (Phase 3): build client, loader, strategy, risk manager, broker,
    # engine; call engine.run(run_id); persist and print report.
    log.warning("backtest_not_implemented", phase="Phase 3")
    return run_id
