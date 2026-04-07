from __future__ import annotations

import json
from pathlib import Path

from cryptobot.core.ids import new_run_id
from cryptobot.monitoring.logging_setup import setup_logging


def test_setup_logging_writes_json_lines(tmp_path: Path):
    run_id = new_run_id("test")
    log = setup_logging(log_dir=tmp_path, level="INFO", run_id=run_id)
    log.info("hello_world", foo=1, bar="baz")

    log_file = tmp_path / f"{run_id}.jsonl"
    assert log_file.exists()
    lines = [line for line in log_file.read_text().splitlines() if line.strip()]
    assert lines, "expected at least one log line"
    parsed = [json.loads(line) for line in lines]
    assert any(row.get("event") == "hello_world" for row in parsed)
    assert all(row.get("run_id") == run_id for row in parsed)
