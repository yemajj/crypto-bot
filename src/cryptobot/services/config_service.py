"""Config display and safe-write helpers.

v1 phasing
──────────
- The Config Editor page ships as **read-only display** first.
- If editing is enabled, only fields in ``EDITABLE_FIELDS`` may be changed.
- Saves always go to a *new* ``<stem>.custom.yaml`` file; the source is never
  overwritten.  The new path should be passed to ``start_paper()`` /
  ``run_backtest_sync()``.

Never-editable: anything in ``EnvSettings`` (API keys, DB URL, tokens, kill-switch path).
"""

from __future__ import annotations

import copy
from pathlib import Path

import structlog
import yaml

from cryptobot.config.settings import RunConfig, load_settings

_log = structlog.get_logger()

# Explicit allowlist — only these dot-separated keys may be written via the UI.
EDITABLE_FIELDS: frozenset[str] = frozenset(
    [
        "strategy.name",
        "strategy.params",
        "risk.max_position_pct",
        "risk.max_gross_exposure_pct",
        "risk.max_daily_loss_pct",
        "risk.max_orders_per_minute",
        "risk.require_stop_loss",
        "risk.max_open_positions",
        "risk.cooldown_after_losses",
        "risk.cooldown_bars",
        "starting_cash",
        "warmup_bars",
        "market.symbols",
        "market.timeframe",
        "fees.taker_bps",
        "fees.maker_bps",
        "fees.slippage_bps",
    ]
)


def load_run_config(config_path: Path) -> RunConfig | None:
    """Load and validate a YAML config.  Returns ``None`` (with a logged error) if invalid."""
    try:
        settings = load_settings(config_path)
        return settings.run
    except Exception as exc:
        _log.error("config_load_failed", path=str(config_path), error=str(exc))
        return None


def save_run_config(source_path: Path, updates: dict) -> Path:
    """Merge *updates* into the YAML at *source_path* and write a new ``*.custom.yaml``.

    Only keys in ``EDITABLE_FIELDS`` are applied; others are silently dropped.
    The merged config is validated via ``RunConfig`` before writing.

    Returns the path of the newly written file.
    Raises ``ValueError`` if the merged config fails pydantic validation.
    """
    source_path = Path(source_path)

    # Load raw YAML (no pydantic yet — preserve unknown keys).
    with source_path.open("r", encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh) or {}

    merged = copy.deepcopy(raw)

    # Apply only allowlisted updates.
    for dotted_key, value in updates.items():
        if dotted_key not in EDITABLE_FIELDS:
            _log.warning("config_edit_rejected_key", key=dotted_key)
            continue
        parts = dotted_key.split(".")
        node = merged
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    # Validate via pydantic before writing.
    try:
        RunConfig(**merged)
    except Exception as exc:
        raise ValueError(f"Config validation failed: {exc}") from exc

    dest = source_path.parent / f"{source_path.stem}.custom.yaml"
    with dest.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(merged, fh, default_flow_style=False, sort_keys=False)

    _log.info("config_saved", dest=str(dest))
    return dest
