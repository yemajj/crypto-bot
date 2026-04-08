from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from cryptobot.cli import app
from cryptobot.journal.models import Run
from cryptobot.journal.writer import (
    build_engine,
    init_db,
    make_session_factory,
    record_equity_snapshot,
    record_run_start,
)

runner = CliRunner()


def _scratch_dir() -> Path:
    path = Path(".tmp") / f"cli-validation-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _seed_validation_run(session_factory, run_id: str, started_at: datetime) -> None:
    notes = (
        "starting_cash=10000.00 config=paper_validation.yaml "
        "validation_profile=paper_validation symbol=BTC/USDT timeframe=5m"
    )
    record_run_start(session_factory, run_id, mode="paper", strategy_name="sma_crossover", notes=notes)
    with session_factory() as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.started_at = started_at
        run.ended_at = started_at + timedelta(hours=1)
        session.commit()
    record_equity_snapshot(session_factory, run_id, started_at, 10050.0, 10050.0)
    record_equity_snapshot(session_factory, run_id, started_at + timedelta(minutes=5), 10100.0, 10100.0)


def test_validate_paper_cli_default(monkeypatch):
    scratch = _scratch_dir()
    db_url = f"sqlite:///{scratch / 'cli.sqlite'}"
    monkeypatch.setenv("CRYPTOBOT_DB_URL", db_url)
    init_db(db_url)
    session_factory = make_session_factory(build_engine(db_url))
    _seed_validation_run(session_factory, "paper_cli_1", datetime.now(timezone.utc) - timedelta(days=1))

    result = runner.invoke(app, ["validate-paper"])
    assert result.exit_code == 0
    assert "PAPER VALIDATION" in result.stdout
    assert "paper_cli_1" in result.stdout


def test_validate_paper_cli_days_option(monkeypatch):
    scratch = _scratch_dir()
    db_url = f"sqlite:///{scratch / 'cli.sqlite'}"
    monkeypatch.setenv("CRYPTOBOT_DB_URL", db_url)
    init_db(db_url)
    session_factory = make_session_factory(build_engine(db_url))
    _seed_validation_run(session_factory, "paper_cli_2", datetime.now(timezone.utc) - timedelta(days=2))

    result = runner.invoke(app, ["validate-paper", "--days", "28"])
    assert result.exit_code == 0
    assert "paper_cli_2" in result.stdout


def test_validate_paper_cli_repeated_run_id(monkeypatch):
    scratch = _scratch_dir()
    db_url = f"sqlite:///{scratch / 'cli.sqlite'}"
    monkeypatch.setenv("CRYPTOBOT_DB_URL", db_url)
    init_db(db_url)
    session_factory = make_session_factory(build_engine(db_url))
    now = datetime.now(timezone.utc)
    _seed_validation_run(session_factory, "paper_cli_a", now - timedelta(days=3))
    _seed_validation_run(session_factory, "paper_cli_b", now - timedelta(days=2))

    result = runner.invoke(
        app,
        ["validate-paper", "--run-id", "paper_cli_a", "--run-id", "paper_cli_b"],
    )
    assert result.exit_code == 0
    assert "paper_cli_a" in result.stdout
    assert "paper_cli_b" in result.stdout
