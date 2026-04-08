from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from cryptobot.analytics.validation import build_validation_report, is_validation_run, parse_notes_metadata
from cryptobot.journal.models import Run
from cryptobot.journal.writer import (
    build_engine,
    init_db,
    make_session_factory,
    record_equity_snapshot,
    record_run_end,
    record_run_start,
)


def _scratch_dir() -> Path:
    path = Path(".tmp") / f"validation-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _make_run(
    session_factory,
    run_id: str,
    *,
    notes: str,
    mode: str = "paper",
    ended: bool = True,
    started_at: datetime | None = None,
    equity_points: list[float] | None = None,
) -> None:
    record_run_start(session_factory, run_id, mode=mode, strategy_name="sma_crossover", notes=notes)
    with session_factory() as session:
        run = session.get(Run, run_id)
        assert run is not None
        if started_at is not None:
            run.started_at = started_at
        if ended:
            run.ended_at = (started_at or run.started_at) + timedelta(hours=1)
        session.commit()

    if equity_points:
        ts0 = started_at or datetime.now(timezone.utc)
        for idx, equity in enumerate(equity_points):
            record_equity_snapshot(
                session_factory,
                run_id,
                ts0 + timedelta(minutes=idx),
                equity,
                equity,
            )

    if ended:
        record_run_end(session_factory, run_id, notes=f"final_equity={(equity_points or [10_000.0])[-1]:.2f}")


def test_parse_notes_metadata_extracts_key_values():
    metadata = parse_notes_metadata(
        "starting_cash=10000.00 config=paper_validation.yaml validation_profile=paper_validation symbol=BTC/USDT timeframe=5m"
    )
    assert metadata["starting_cash"] == "10000.00"
    assert metadata["config"] == "paper_validation.yaml"
    assert metadata["validation_profile"] == "paper_validation"
    assert metadata["symbol"] == "BTC/USDT"
    assert metadata["timeframe"] == "5m"


def test_is_validation_run_requires_completed_tagged_paper_run():
    run = Run(
        id="paper_x",
        mode="paper",
        strategy="sma_crossover",
        started_at=datetime.now(timezone.utc),
        ended_at=datetime.now(timezone.utc),
        notes="validation_profile=paper_validation",
    )
    assert is_validation_run(run) is True
    run.ended_at = None
    assert is_validation_run(run) is False


def test_build_validation_report_excludes_untagged_and_running_runs():
    scratch = _scratch_dir()
    db_url = f"sqlite:///{scratch / 'validation.sqlite'}"
    init_db(db_url)
    session_factory = make_session_factory(build_engine(db_url))
    now = datetime.now(timezone.utc)

    _make_run(
        session_factory,
        "paper_tagged",
        notes="starting_cash=10000.00 config=paper_validation.yaml validation_profile=paper_validation symbol=BTC/USDT timeframe=5m",
        started_at=now - timedelta(days=1),
        equity_points=[10050.0, 10100.0, 10150.0],
    )
    _make_run(
        session_factory,
        "paper_dev",
        notes="starting_cash=10000.00 config=paper_fast.yaml symbol=BTC/USDT timeframe=5m",
        started_at=now - timedelta(days=1),
        equity_points=[10010.0, 10020.0],
    )
    _make_run(
        session_factory,
        "paper_running",
        notes="starting_cash=10000.00 config=paper_validation.yaml validation_profile=paper_validation symbol=BTC/USDT timeframe=5m",
        started_at=now - timedelta(hours=2),
        ended=False,
        equity_points=[9990.0],
    )

    report = build_validation_report(db_url, days=14)
    assert "paper_tagged" in report
    assert "paper_dev" not in report
    assert "paper_running" not in report


def test_build_validation_report_explicit_run_ids_override_tag_filter():
    scratch = _scratch_dir()
    db_url = f"sqlite:///{scratch / 'validation.sqlite'}"
    init_db(db_url)
    session_factory = make_session_factory(build_engine(db_url))
    now = datetime.now(timezone.utc)

    _make_run(
        session_factory,
        "paper_manual",
        notes="starting_cash=10000.00 config=paper.yaml symbol=BTC/USDT timeframe=5m",
        started_at=now - timedelta(days=1),
        equity_points=[10025.0, 10040.0],
    )

    report = build_validation_report(db_url, run_ids=["paper_manual"])
    assert "paper_manual" in report
    assert "CONTRIBUTING RUNS" in report


def test_build_validation_report_handles_no_validation_runs():
    scratch = _scratch_dir()
    db_url = f"sqlite:///{scratch / 'validation.sqlite'}"
    init_db(db_url)
    report = build_validation_report(db_url, days=14)
    assert "No completed validation paper runs found" in report


def test_build_validation_report_shows_manual_checks_pending():
    scratch = _scratch_dir()
    db_url = f"sqlite:///{scratch / 'validation.sqlite'}"
    init_db(db_url)
    session_factory = make_session_factory(build_engine(db_url))
    now = datetime.now(timezone.utc)

    _make_run(
        session_factory,
        "paper_tagged",
        notes="starting_cash=10000.00 config=paper_validation.yaml validation_profile=paper_validation symbol=BTC/USDT timeframe=5m",
        started_at=now - timedelta(days=1),
        equity_points=[10050.0, 10100.0, 10150.0],
    )

    report = build_validation_report(db_url, days=14)
    assert "[PENDING] Kill switch manually tested" in report
    assert "[PENDING] Telegram alerts verified" in report
