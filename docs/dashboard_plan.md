# Crypto Bot Dashboard MVP Plan

## Objective

Build a Streamlit dashboard to operate the bot (paper trading + backtesting)
without touching the terminal, while keeping a clean architecture for future
migration to FastAPI/React.

## Key Principle

**Streamlit = UI only.** All logic lives in reusable service modules that have
no Streamlit dependency. The UI calls services; services call existing
`app/`, `analytics/`, `journal/`, and `config/` modules.

---

## Architecture

```
Streamlit pages
      │
      ▼
src/cryptobot/services/        ← NEW (3 files only)
  run_service.py               ← thread lifecycle + status model
  log_service.py               ← JSONL reader
  config_service.py            ← load + safe-write RunConfig YAML

      │  calls into existing modules (no changes needed):
      ▼
analytics/queries.py           ← metrics, trades, equity curve
analytics/report.py            ← run reports
journal/writer.py              ← DB engine + record helpers
config/settings.py             ← load_settings(), pydantic validation
app/run_backtest.py            ← main(config_path, data_path)
app/run_paper.py               ← main(config_path)
```

CLI entry points (`cli.py`) are unchanged — they already call `app/` directly.

### Process Management (paper trading)

`run_paper.main()` is a blocking loop; it must not run on Streamlit's main
thread. Use `threading.Thread` + `threading.Event`:

```python
# run_service.py (conceptual sketch)
_stop_event: threading.Event | None = None
_run_thread: threading.Thread | None = None

def start_paper(config_path) -> str:          # returns run_id
def stop_paper(timeout_s: float = 10) -> None # sets event, joins thread
def paper_status() -> RunStatus               # reads thread + last DB snapshot
```

Thread and event live as **module-level singletons** so Streamlit reruns do not
re-spawn them. Paper run survives page refresh.

`run_paper.main()` must accept a `stop_event: threading.Event` parameter (or
poll it via an injected callback) so it exits cleanly when signalled.

### FastAPI Migration Safety

Services must be **stateless across requests** (no `st.session_state`). All
persistent state flows through SQLite. Migration = replace Streamlit pages with
FastAPI route handlers that call the same service functions.

---

## Service Layer: What to Build vs. What Already Exists

| Concern | Module | Action |
|---------|--------|--------|
| Metrics, trades, equity | `analytics/queries.py` | **Use as-is** |
| Run reports | `analytics/report.py` | **Use as-is** |
| DB setup | `journal/writer.py` | **Use as-is** |
| Config load + validation | `config/settings.py` | **Use as-is** |
| Paper/backtest entry points | `app/run_*.py` | **Minor change**: add `stop_event` param to `run_paper.main()` |
| Paper run lifecycle | `services/run_service.py` | **Write** |
| Log reading | `services/log_service.py` | **Write** |
| Config safe-write | `services/config_service.py` | **Write** |

### run_service.py

```python
@dataclass
class RunStatus:
    active: bool
    run_id: str | None
    last_bar_ts: datetime | None   # from last equity_snapshot row
    equity: float | None
    mode: str | None               # "paper" | "backtest" | None

def start_paper(config_path: Path) -> str        # spawns thread, returns run_id
def stop_paper(timeout_s: float = 10) -> None    # signals + joins
def paper_status() -> RunStatus                  # thread.is_alive() + DB query
def run_backtest(config_path: Path, data_path: Path) -> BacktestResult  # blocking (backtest is fast)
```

### log_service.py

```python
def tail_run_log(run_id: str, log_dir: Path, n: int = 200) -> list[dict]
    # Reads logs/{run_id}.jsonl, returns last n parsed JSON lines
```

### config_service.py

```python
# Editable fields: strategy.params, risk.*, starting_cash, warmup_bars
# Locked fields: anything in EnvSettings (API keys, DB URL, tokens)
EDITABLE_FIELDS: frozenset[str]  # explicit allowlist

def load_run_config(config_path: Path) -> RunConfig
def save_run_config(config_path: Path, updates: dict) -> None
    # Validates updates against EDITABLE_FIELDS before writing
    # Re-validates via RunConfig(**merged) before saving to disk
```

### Kill Switch

No dedicated service needed — expose three one-liners from `run_service.py`:

```python
def kill_switch_arm(path: Path) -> None:    Path(path).touch()
def kill_switch_disarm(path: Path) -> None: Path(path).unlink(missing_ok=True)
def kill_switch_active(path: Path) -> bool: return Path(path).exists()
```

---

## MVP Features

| Feature | Source |
|---------|--------|
| Start paper trading | `run_service.start_paper()` |
| Stop paper trading | `run_service.stop_paper()` |
| View run status | `run_service.paper_status()` |
| Toggle kill switch | `run_service.kill_switch_{arm,disarm,active}()` |
| Launch backtest (with CSV path input) | `run_service.run_backtest()` |
| Select/edit config (safe fields only) | `config_service.load/save_run_config()` |
| View equity chart | `analytics.queries.get_equity_curve()` |
| View trades table | `analytics.queries.reconstruct_trades()` |
| View daily PnL | `analytics.queries.daily_summary()` |
| View logs | `log_service.tail_run_log()` |
| Live trading disabled | `app/run_live.py` raises `LiveTradingDisabled` — no UI for it |

**Editable config fields (explicit allowlist):**
`strategy.name`, `strategy.params.*`, `risk.max_position_pct`,
`risk.max_gross_exposure_pct`, `risk.max_daily_loss_pct`,
`risk.max_orders_per_minute`, `risk.require_stop_loss`,
`risk.max_open_positions`, `risk.cooldown_after_losses`, `risk.cooldown_bars`,
`starting_cash`, `warmup_bars`, `market.symbols`, `market.timeframe`,
`fees.taker_bps`, `fees.maker_bps`, `fees.slippage_bps`

**Never editable via UI:** anything in `EnvSettings` (API keys, DB URL,
Telegram tokens, kill switch path).

---

## Constraints

- Do not break CLI (`cryptobot backtest`, `cryptobot paper`, `cryptobot live`)
- Do not expose `EnvSettings` secrets in UI
- Do not bypass the risk manager
- All safety controls (`KillSwitchFile`, `RequireStopLoss`) remain active
- No overengineering — no async, no message queues, no Redis

---

## Implementation Steps

1. **Add `stop_event` param to `run_paper.main()`** — poll it in the bar loop
   alongside the existing kill-switch check. This is the only change to
   existing `app/` code.

2. **Write `src/cryptobot/services/`** — `run_service.py`, `log_service.py`,
   `config_service.py` (see specs above).

3. **Build Streamlit app** at `src/cryptobot/dashboard/` with pages:
   - `Home` — status card, kill switch toggle
   - `Paper Trading` — start/stop, live equity chart (auto-refresh)
   - `Backtest` — config picker, CSV path input, run + results
   - `Config Editor` — safe-fields form with pydantic validation feedback
   - `Logs` — last N log lines with level filter
   - `History` — run list → drill into trades/metrics

4. **Add `cryptobot dashboard` CLI command** in `cli.py` that runs
   `streamlit run src/cryptobot/dashboard/app.py`.

5. **Add tests** for all three service modules (mock `app/` entry points).

6. **Update README** with dashboard section.

---

## Future Path

- Replace Streamlit pages with FastAPI route handlers + React frontend
- Service layer is reused entirely; only the transport layer changes
- Thread-based process management can be replaced with subprocess + PID file
  at that stage for better process isolation

---

## Acceptance Criteria

- Bot can be fully operated without a terminal
- CLI commands still work unchanged
- Paper run survives Streamlit page refresh (thread is a module-level singleton)
- Kill switch file appears/disappears correctly when toggled from UI
- Backtest results display after CSV path is provided and run completes
- Config edits are validated before saving; secrets are never shown
- `pytest tests/` passes after all changes
- `cryptobot live` still raises `LiveTradingDisabled`
