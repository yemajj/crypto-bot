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
def report(
    run_id: str | None = typer.Option(None, "--run-id", help="Run ID to report on (default: latest)."),
    list_runs: bool = typer.Option(False, "--list", help="List recent runs instead of reporting."),
) -> None:
    """Print a performance report for a backtest or paper run."""
    from cryptobot.analytics.report import build_report, list_runs_report
    from cryptobot.journal.writer import build_engine, make_session_factory
    from cryptobot.analytics.queries import list_runs as _list_runs

    settings = load_settings()
    db_url = settings.env.db_url

    if list_runs:
        typer.echo(list_runs_report(db_url))
        return

    if run_id is None:
        # Resolve the most recent run.
        eng = build_engine(db_url)
        sf = make_session_factory(eng)
        runs = _list_runs(sf, n=1)
        if not runs:
            typer.echo("No runs found in the journal. Run a backtest or paper session first.", err=True)
            raise typer.Exit(code=1)
        run_id = runs[0].id

    r = build_report(run_id, db_url)
    typer.echo(r.summary)


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
