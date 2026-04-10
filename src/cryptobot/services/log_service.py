"""Read structured log files written by the run loops.

Each run writes a JSONL file at ``{log_dir}/{run_id}.jsonl``.
"""

from __future__ import annotations

import json
from pathlib import Path


def tail_run_log(run_id: str, log_dir: Path, n: int = 200) -> list[dict]:
    """Return the last *n* parsed log lines for *run_id*.

    Returns an empty list if the log file does not exist yet.
    Lines that fail JSON parsing are skipped.
    """
    log_path = Path(log_dir) / f"{run_id}.jsonl"
    if not log_path.exists():
        return []

    lines: list[dict] = []
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
    except OSError:
        return []

    return lines[-n:]
