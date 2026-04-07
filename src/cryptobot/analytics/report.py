"""Per-run reporting (Phase 7 focus).

For Phase 1 we only expose a stub so the CLI can wire to it later.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunReport:
    run_id: str
    summary: str


def build_report(run_id: str, db_url: str) -> RunReport:
    # TODO (Phase 7): read runs/signals/orders/fills, compute PnL and metrics,
    # render a concise text (and optionally CSV) report.
    return RunReport(run_id=run_id, summary="report not yet implemented")
