"""Journal writer: thin wrapper around SQLAlchemy sessions.

Phase 1 ships `init_db` so a fresh install can create tables. Per-entity
writers (record_signal, record_order, record_fill) land alongside the
backtest engine in Phase 3.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from cryptobot.journal.models import Base


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


# TODO (Phase 3): record_run_start, record_signal, record_order, record_fill,
# record_run_end — all idempotent and small.
