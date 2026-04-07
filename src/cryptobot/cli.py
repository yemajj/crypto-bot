"""Typer CLI entry point.

Keeps the surface tiny in v1:
    cryptobot version
    cryptobot init-db
    cryptobot backtest --config config/backtest.yaml   (Phase 3)
    cryptobot paper    --config config/paper.yaml      (Phase 4)
    cryptobot live                                     (disabled, Phase 6)
"""

from __future__ import annotations

from pathlib import Path

import typer

from cryptobot import __version__
from cryptobot.app import run_backtest, run_live, run_paper
from cryptobot.config import load_settings
from cryptobot.journal.writer import init_db

app = typer.Typer(
    add_completion=False,
    help="Safety-first crypto bot (research / backtest / paper).",
)


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(f"cryptobot {__version__}")


@app.command("init-db")
def init_db_cmd() -> None:
    """Create SQLite tables for the trade journal."""
    settings = load_settings()
    init_db(settings.env.db_url)
    typer.echo(f"initialized db at {settings.env.db_url}")


@app.command()
def backtest(
    config: Path = typer.Option(..., exists=True, help="Path to backtest YAML."),
    data: Path = typer.Option(..., exists=True, help="Path to OHLCV CSV file."),
) -> None:
    """Run a backtest from a local OHLCV CSV file."""
    run_id = run_backtest.main(config, data)
    typer.echo(f"backtest complete: run_id={run_id}")


@app.command()
def paper(
    config: Path = typer.Option(..., exists=True, help="Path to paper YAML."),
) -> None:
    """Run paper trading. (Phase 4 implementation.)"""
    run_id = run_paper.main(config)
    typer.echo(f"paper run_id={run_id}")


@app.command()
def live() -> None:
    """Refuses to run in v1 — live trading is disabled by design."""
    try:
        run_live.main("unused")
    except run_live.LiveTradingDisabled as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


if __name__ == "__main__":  # pragma: no cover
    app()
