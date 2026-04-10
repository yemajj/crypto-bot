"""Tests for cryptobot.services.run_service."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cryptobot.services import run_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_service_state() -> None:
    """Reset module-level singletons between tests."""
    run_service._run_thread = None
    run_service._stop_event = None
    run_service._current_run_id = None
    run_service._current_mode = None
    run_service._last_error = None
    run_service._db_url = None


# ---------------------------------------------------------------------------
# kill-switch helpers
# ---------------------------------------------------------------------------

def test_kill_switch_arm_disarm(tmp_path: Path) -> None:
    ks = tmp_path / "KILL_SWITCH"
    assert not run_service.kill_switch_active(ks)
    run_service.kill_switch_arm(ks)
    assert run_service.kill_switch_active(ks)
    run_service.kill_switch_disarm(ks)
    assert not run_service.kill_switch_active(ks)


def test_kill_switch_disarm_idempotent(tmp_path: Path) -> None:
    ks = tmp_path / "KILL_SWITCH"
    # disarm on non-existent file must not raise
    run_service.kill_switch_disarm(ks)


# ---------------------------------------------------------------------------
# paper_status when idle
# ---------------------------------------------------------------------------

def test_paper_status_idle() -> None:
    _reset_service_state()
    status = run_service.paper_status()
    assert not status.active
    assert status.run_id is None
    assert status.equity is None
    assert status.error is None


# ---------------------------------------------------------------------------
# start_paper — happy path (mock run_paper.main)
# ---------------------------------------------------------------------------

def _fake_main(config_path, stop_event=None, run_id=None):
    """Simulates a paper run that waits for stop_event."""
    if stop_event is not None:
        stop_event.wait(timeout=5.0)


def test_start_paper_spawns_thread(tmp_path: Path) -> None:
    _reset_service_state()
    config = tmp_path / "paper.yaml"
    config.touch()

    with patch.object(run_service._paper_mod, "main", side_effect=_fake_main):
        run_id = run_service.start_paper(config, "sqlite:///:memory:")

    assert run_id.startswith("paper-")
    assert run_service._run_thread is not None
    assert run_service._run_thread.is_alive()

    # Clean up
    run_service.stop_paper(timeout_s=2.0)
    _reset_service_state()


def test_start_paper_raises_if_already_active(tmp_path: Path) -> None:
    _reset_service_state()
    config = tmp_path / "paper.yaml"
    config.touch()

    with patch.object(run_service._paper_mod, "main", side_effect=_fake_main):
        run_service.start_paper(config, "sqlite:///:memory:")
        with pytest.raises(RuntimeError, match="already active"):
            run_service.start_paper(config, "sqlite:///:memory:")

    run_service.stop_paper(timeout_s=2.0)
    _reset_service_state()


# ---------------------------------------------------------------------------
# stop_paper
# ---------------------------------------------------------------------------

def test_stop_paper_signals_thread(tmp_path: Path) -> None:
    _reset_service_state()
    config = tmp_path / "paper.yaml"
    config.touch()

    with patch.object(run_service._paper_mod, "main", side_effect=_fake_main):
        run_service.start_paper(config, "sqlite:///:memory:")
        assert run_service._run_thread is not None
        assert run_service._run_thread.is_alive()

        run_service.stop_paper(timeout_s=3.0)
        assert not run_service._run_thread.is_alive()

    _reset_service_state()


# ---------------------------------------------------------------------------
# Thread error capture
# ---------------------------------------------------------------------------

def _crashing_main(config_path, stop_event=None, run_id=None):
    raise ValueError("boom")


def test_thread_error_captured_in_status(tmp_path: Path) -> None:
    _reset_service_state()
    config = tmp_path / "paper.yaml"
    config.touch()

    with patch.object(run_service._paper_mod, "main", side_effect=_crashing_main):
        run_service.start_paper(config, "sqlite:///:memory:")
        # Wait briefly for the thread to crash
        time.sleep(0.3)

    status = run_service.paper_status()
    assert not status.active
    assert status.error is not None
    assert "boom" in status.error

    _reset_service_state()
