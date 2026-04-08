"""Typer CLI entry point.

Keeps the surface tiny in v1:
    cryptobot version
    cryptobot init-db
    cryptobot backtest       --config config/backtest.yaml   (Phase 3)
    cryptobot walk-forward   --config config/backtest.yaml --data ...
    cryptobot paper          --config config/paper.yaml      (Phase 4)
    cryptobot live                                           (disabled, Phase 6)
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


@app.command("walk-forward")
def walk_forward(
    config: Path = typer.Option(..., exists=True, help="Path to backtest YAML."),
    data: Path = typer.Option(..., exists=True, help="Path to OHLCV CSV file."),
    folds: int = typer.Option(5, help="Number of rolling folds."),
    in_sample_pct: float = typer.Option(0.7, help="Fraction of each fold used for in-sample."),
) -> None:
    """Run a rolling walk-forward validation on a local OHLCV CSV file."""
    from cryptobot.backtest.walk_forward import print_walk_forward_report, run_walk_forward
    from cryptobot.app.run_backtest import load_bars_from_csv

    settings = load_settings(config)
    symbol = settings.run.market.symbols[0]
    timeframe = settings.run.market.timeframe

    typer.echo(f"Loading bars from {data} …")
    bars = load_bars_from_csv(data, symbol, timeframe)
    typer.echo(f"Loaded {len(bars):,} bars. Running {folds}-fold walk-forward …")

    try:
        results = run_walk_forward(settings, bars, folds=folds, in_sample_pct=in_sample_pct)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    print_walk_forward_report(results, symbol, timeframe)


@app.command()
def paper(
    config: Path = typer.Option(..., exists=True, help="Path to paper YAML."),
) -> None:
    """Run paper trading. (Phase 4 implementation.)"""
    run_id = run_paper.main(config)
    typer.echo(f"paper run_id={run_id}")


@app.command("fetch-history")
def fetch_history(
    symbol: str = typer.Option(..., help="Trading pair, e.g. BTC/USDT."),
    timeframe: str = typer.Option("1h", help="Bar timeframe, e.g. 1h, 4h, 1d."),
    since: str = typer.Option(..., help="Start date (UTC), e.g. 2024-01-01."),
    until: str | None = typer.Option(None, help="End date (UTC, exclusive). Defaults to now."),
) -> None:
    """Fetch historical OHLCV bars from the exchange and store them locally."""
    from datetime import datetime, timezone

    from cryptobot.data.loader import HistoricalLoader
    from cryptobot.data.storage import BarStore
    from cryptobot.exchanges.ccxt_client import CcxtClient

    settings = load_settings()

    try:
        since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        typer.echo(f"error: --since must be YYYY-MM-DD, got {since!r}", err=True)
        raise typer.Exit(code=1)

    until_dt: datetime | None = None
    if until is not None:
        try:
            until_dt = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            typer.echo(f"error: --until must be YYYY-MM-DD, got {until!r}", err=True)
            raise typer.Exit(code=1)

    client = CcxtClient(
        name=settings.env.exchange_name,
        api_key=settings.env.exchange_api_key,
        api_secret=settings.env.exchange_api_secret,
        testnet=settings.env.exchange_testnet,
    )
    bar_store = BarStore(settings.env.data_dir)
    loader = HistoricalLoader(client, bar_store)

    typer.echo(f"Fetching {symbol} {timeframe} from {since}" + (f" to {until}" if until else " to now") + " …")
    n = loader.fetch(symbol, timeframe, since=since_dt, until=until_dt)
    typer.echo(f"Done. {n:,} bars written to {settings.env.data_dir}")


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
