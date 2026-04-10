"""Tests for cryptobot.services.log_service."""

from __future__ import annotations

import json
from pathlib import Path

from cryptobot.services.log_service import tail_run_log


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def test_tail_returns_empty_when_file_missing(tmp_path: Path) -> None:
    result = tail_run_log("no-such-run", tmp_path, n=10)
    assert result == []


def test_tail_returns_all_lines_when_fewer_than_n(tmp_path: Path) -> None:
    records = [{"event": f"msg_{i}", "level": "info"} for i in range(5)]
    _write_jsonl(tmp_path / "myrun.jsonl", records)
    result = tail_run_log("myrun", tmp_path, n=100)
    assert len(result) == 5
    assert result[0]["event"] == "msg_0"


def test_tail_respects_n(tmp_path: Path) -> None:
    records = [{"event": f"msg_{i}", "level": "info"} for i in range(50)]
    _write_jsonl(tmp_path / "run42.jsonl", records)
    result = tail_run_log("run42", tmp_path, n=10)
    assert len(result) == 10
    # Should be the last 10
    assert result[0]["event"] == "msg_40"
    assert result[-1]["event"] == "msg_49"


def test_tail_skips_invalid_json_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "run99.jsonl"
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write('{"event": "good1"}\n')
        fh.write("not-json-at-all\n")
        fh.write('{"event": "good2"}\n')
    result = tail_run_log("run99", tmp_path, n=100)
    assert len(result) == 2
    assert result[0]["event"] == "good1"
    assert result[1]["event"] == "good2"


def test_tail_empty_file(tmp_path: Path) -> None:
    (tmp_path / "emptyrun.jsonl").touch()
    result = tail_run_log("emptyrun", tmp_path, n=10)
    assert result == []
