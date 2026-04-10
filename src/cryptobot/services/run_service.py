"""Paper-run and backtest lifecycle management.

Module-level singletons (``_run_thread``, ``_stop_event``, …) survive Streamlit
page reruns because Python caches imported modules in ``sys.modules``.  The
paper run therefore outlives any individual page render.

v1 constraint: only one active run at a time (paper or backtest, never concurrent).
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass
from pathlib import Path

import structlog

from cryptobot.analytics.queries import get_equity_curve
from cryptobot.app import run_backtest as _backtest_mod
from cryptobot.app import run_paper as _paper_mod
from cryptobot.core.ids import new_run_id
from cryptobot.journal.writer import build_engine, make_session_factory

_log = structlog.get_logger()


@dataclass
class RunStatus:
    active: bool
    run_id: str | None
    mode: str | None          # "paper" | "backtest" | None
    equity: float | None
    error: str | None         # populated if the thread exited with an exception


# ---------------------------------------------------------------------------
# Module-level singletons — survive Streamlit reruns
# ---------------------------------------------------------------------------

_lock: threading.Lock = threading.Lock()
_stop_event: threading.Event | None = None
_run_thread: threading.Thread | None = None
_current_run_id: str | None = None
_current_mode: str | None = None
_last_error: str | None = None
_db_url: str | None = None


# ---------------------------------------------------------------------------
# Internal thread target
# ---------------------------------------------------------------------------

def _paper_target(config_path: Path, stop_event: threading.Event, run_id: str) -> None:
    global _last_error
    try:
        _paper_mod.main(config_path, stop_event=stop_event, run_id=run_id)
    except Exception as exc:
        with _lock:
            _last_error = traceback.format_exc()
        _log.error("paper_thread_crashed", error=str(exc))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_paper(config_path: Path, db_url: str) -> str:
    """Spawn a paper-trading thread.  Returns the pre-generated run_id.

    Raises ``RuntimeError`` if a run is already active.
    """
    global _stop_event, _run_thread, _current_run_id, _current_mode, _last_error, _db_url

    with _lock:
        if _run_thread is not None and _run_thread.is_alive():
            raise RuntimeError("A run is already active; stop it before starting a new one.")

        run_id = new_run_id("paper")
        _stop_event = threading.Event()
        _current_run_id = run_id
        _current_mode = "paper"
        _last_error = None
        _db_url = db_url

        _run_thread = threading.Thread(
            target=_paper_target,
            args=(config_path, _stop_event, run_id),
            daemon=True,
            name="paper-run",
        )
        _run_thread.start()

    return run_id


def stop_paper(timeout_s: float = 10.0) -> None:
    """Signal the paper thread to stop and wait up to ``timeout_s`` seconds."""
    with _lock:
        ev = _stop_event
        thr = _run_thread

    if ev is not None:
        ev.set()
    if thr is not None:
        thr.join(timeout=timeout_s)


def paper_status() -> RunStatus:
    """Return current run state.  Safe to call on every Streamlit rerun."""
    with _lock:
        active = _run_thread is not None and _run_thread.is_alive()
        run_id = _current_run_id
        mode = _current_mode if active else None
        error = _last_error
        db = _db_url

    equity: float | None = None
    if run_id is not None and db is not None:
        try:
            engine = build_engine(db)
            sf = make_session_factory(engine)
            curve = get_equity_curve(sf, run_id)
            if curve:
                equity = curve[-1]
        except Exception:
            pass  # DB not ready yet; surface nothing rather than crashing the UI

    return RunStatus(active=active, run_id=run_id, mode=mode, equity=equity, error=error)


def run_backtest_sync(config_path: Path, data_path: Path) -> str:
    """Run a backtest synchronously (blocking).  Returns run_id."""
    return _backtest_mod.main(config_path, data_path)


# ---------------------------------------------------------------------------
# Kill-switch helpers
# ---------------------------------------------------------------------------

def kill_switch_arm(path: Path) -> None:
    Path(path).touch()


def kill_switch_disarm(path: Path) -> None:
    Path(path).unlink(missing_ok=True)


def kill_switch_active(path: Path) -> bool:
    return Path(path).exists()
