"""Structured logging via structlog.

One setup function to be called exactly once at application startup. Emits
JSON lines to both stdout and a per-run log file under `logs/`. Every log
entry carries the `run_id` so the journal can be reconstructed later.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog


def setup_logging(
    *,
    log_dir: Path,
    level: str = "INFO",
    run_id: str,
) -> structlog.stdlib.BoundLogger:
    """Configure structlog + stdlib logging and return a bound logger.

    Safe to call more than once in tests; subsequent calls will reconfigure.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{run_id}.jsonl"

    # stdlib root logger: JSON to file, plain line to stderr
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(stderr_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logger: structlog.stdlib.BoundLogger = structlog.get_logger("cryptobot").bind(
        run_id=run_id
    )
    logger.info("logging_initialized", log_file=str(log_file), level=level)
    return logger


def get_logger(**bindings: Any) -> structlog.stdlib.BoundLogger:
    """Convenience accessor for modules that just need a logger."""
    return structlog.get_logger("cryptobot").bind(**bindings)
