"""Journal writer: thin wrapper around SQLAlchemy sessions.

All `record_*` functions accept a `sessionmaker` factory and are idempotent
where possible. Each function opens its own session so that a failure in one
event does not roll back unrelated writes.

Duplicate primary-key inserts (e.g. same order_id submitted twice) are logged
and silently skipped rather than crashing the run loop.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker, Session

from cryptobot.core.types import Fill, Order, Signal
from cryptobot.journal.models import Base, FillRow, OrderRow, Run, SignalRow
from cryptobot.monitoring.logging_setup import get_logger
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

log = get_logger(component="journal")


def build_engine(db_url: str) -> Engine:
    # `check_same_thread=False` is safe for our single-writer usage of SQLite.
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, future=True, connect_args=connect_args)


def init_db(db_url: str) -> Engine:
    """Create all tables if they do not yet exist."""
    engine = build_engine(db_url)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


# ---------------------------------------------------------------------------
# Record functions
# ---------------------------------------------------------------------------

def record_run_start(
    factory: sessionmaker[Session],
    run_id: str,
    mode: str,
    strategy_name: str,
    notes: str | None = None,
) -> None:
    """Insert a new Run row. Idempotent: skips silently if run_id already exists."""
    with factory() as session:
        try:
            session.add(
                Run(
                    id=run_id,
                    mode=mode,
                    strategy=strategy_name,
                    started_at=datetime.now(timezone.utc),
                    notes=notes,
                )
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            log.warning("journal_run_already_exists", run_id=run_id)


def record_signal(
    factory: sessionmaker[Session],
    run_id: str,
    signal: Signal,
) -> None:
    """Insert a SignalRow. Non-idempotent (signals have auto-increment PK)."""
    with factory() as session:
        session.add(
            SignalRow(
                run_id=run_id,
                strategy=signal.strategy_id,
                symbol=signal.symbol,
                ts=signal.ts,
                strength=signal.strength,
                reason=signal.reason,
            )
        )
        session.commit()


def record_order(
    factory: sessionmaker[Session],
    run_id: str,
    order: Order,
) -> None:
    """Insert an OrderRow. Idempotent: skips if order_id already recorded."""
    with factory() as session:
        try:
            session.add(
                OrderRow(
                    id=order.order_id,
                    run_id=run_id,
                    strategy=order.strategy_id,
                    symbol=order.symbol,
                    side=order.side.value,
                    qty=float(order.qty),
                    order_type=order.order_type.value,
                    limit_price=(
                        float(order.limit_price) if order.limit_price is not None else None
                    ),
                    status=order.status.value,
                    ts_submitted=order.ts_submitted,
                )
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            log.warning("journal_order_duplicate", order_id=order.order_id)


def record_fill(
    factory: sessionmaker[Session],
    fill: Fill,
) -> None:
    """Insert a FillRow. Non-idempotent (fills have auto-increment PK)."""
    with factory() as session:
        session.add(
            FillRow(
                order_id=fill.order_id,
                ts=fill.ts,
                price=float(fill.price),
                qty=float(fill.qty),
                fee=float(fill.fee),
                fee_currency=fill.fee_currency,
            )
        )
        session.commit()


def record_run_end(
    factory: sessionmaker[Session],
    run_id: str,
    notes: str | None = None,
) -> None:
    """Set ended_at on the Run row. No-op if run_id not found."""
    with factory() as session:
        run = session.get(Run, run_id)
        if run is not None:
            run.ended_at = datetime.now(timezone.utc)
            if notes:
                existing = run.notes or ""
                run.notes = f"{existing} {notes}".strip()
            session.commit()
        else:
            log.warning("journal_run_not_found_on_end", run_id=run_id)
