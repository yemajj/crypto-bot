# Next Session Plan — Safe Work Items While Bot Runs

> Generated: 2026-04-08  
> Context: Paper trading bot is running (`config/paper_fast.yaml`, SMA 5/15 on 5m bars).
> All Python source files, tests, config files, and docs are safe to modify — the running
> process loaded them at startup and will not reload them mid-run. The only files that must
> NOT be touched are `data/cryptobot.sqlite`, `data/*.parquet`, and `logs/`.

---

## Available Work Items

### 1. Walk-Forward Backtest Utility (post-Phase 7)

**Priority: High** — fills a real gap before live trading is considered.

**Why it matters:**  
A single backtest on a fixed window can badly overfit. Walk-forward validation splits the
historical bar series into N folds, runs a fresh backtest on each in-sample window, then
evaluates on the held-out out-of-sample window. If metrics collapse out-of-sample, the
strategy has no real edge.

**Scope constraints (v1 — keep it narrow):**
- Single strategy only (no parameter sweeping or optimization)
- Single symbol only (matches existing backtest engine behavior)
- No new framework — reuse `BacktestEngine` and `BacktestResult` directly
- Prefer cached/local historical data (parquet cache or CSV); no live exchange fetching

**New files:**

| File | Purpose |
|---|---|
| `src/cryptobot/backtest/walk_forward.py` | `WalkForwardEngine`: slices bars into folds, runs `BacktestEngine` on each, returns `list[FoldResult]` |
| New CLI command in `src/cryptobot/cli.py` | `cryptobot walk-forward --config ... --data ... --folds N [--in-sample-pct 0.7]` |

**Reuse (do not rewrite):**
- `src/cryptobot/backtest/engine.py` — existing `BacktestEngine`, bar-loop logic unchanged
- `src/cryptobot/backtest/metrics.py` — existing `BacktestResult`, returned per fold
- `src/cryptobot/app/run_backtest.py` — reference for how engine is wired today

**Output per fold:**
```
Fold 1/5  in-sample: 2024-01-01→2024-06-01  out-of-sample: 2024-06-01→2024-08-01
  In-sample   Sharpe=1.42  MaxDD=8.1%  WinRate=54%  Trades=18
  Out-sample  Sharpe=0.91  MaxDD=12.3% WinRate=48%  Trades=7
```

**Verification:**
```bash
cryptobot walk-forward --config config/backtest.yaml --data path/to/ohlcv.csv --folds 5
```

**Tests to add:** `tests/test_walk_forward.py` — fold slicing correctness, metric aggregation,
edge cases (too-few bars for requested folds).

**Architecture constraint (from `CLAUDE.md`):**  
Bar-loop ordering is load-bearing: `settle fills → check stops → mark equity → strategy → risk → submit`.
Each fold must construct a fresh `BacktestEngine`, `BacktestBroker`, and `RiskManager` — no shared
state between folds.

---

### 2. Windows CLI Unicode Fix (minor quality-of-life)

**Priority: Medium** — affects developer experience on Windows only.

**Problem:**  
`cryptobot report` crashes on Windows with `UnicodeEncodeError: 'charmap' codec can't encode
character '\u2713'` (✓/✗ symbols in the pre-live checklist). Current workaround:
```bash
PYTHONIOENCODING=utf-8 cryptobot report
```

**Fix options (pick one):**

**Option A (preferred):** Replace ✓/✗ in `analytics/report.py` with ASCII equivalents `[OK]`/`[FAIL]`.  
- One file changed, zero risk, works everywhere.
- Trade-off: slightly less pretty output.

**Option B:** Reconfigure stdout at the CLI entry point in `cli.py`:
```python
import sys, io
if sys.platform == "win32" and getattr(sys.stdout, "encoding", "utf-8") != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
```
- Keeps the symbols, fixes only the encoding path.
- Slightly more complex; needs the `TextIOWrapper` approach to work correctly with Typer.

**Verification:** Run `cryptobot report` on Windows without `PYTHONIOENCODING=utf-8`. Should not crash.

---

### 3. `n_bars_held` Comment (trivial)

**Priority: Low** — one-line comment, no logic change.

**File:** `src/cryptobot/analytics/queries.py`, `reconstruct_trades()`.

**Issue:** `n_bars_held=0` is hardcoded because the DB schema records fills (not bar entry/exit
timestamps) and reconstructing bar count requires knowing the timeframe, which the analytics
layer doesn't currently receive. Not a bug — PnL is correct — but a future reader will be confused.

**Fix:**
```python
n_bars_held=0,  # TODO: derive from fill timestamps + timeframe once timeframe is in journal
```

---

### 4. Verify README and plan.md Status Wording Are Consistent

**Priority: Low** — housekeeping, prevents confusion when sharing with others.

Read `README.md` and `plan.md` side-by-side and check that phase descriptions and completion
markers agree. Adjust only if there is an actual discrepancy — no edits needed if they already
match.

---

### 5. Phase 8 — AI-Assisted Research Tools (deferred)

**Do not start this yet.** No AI feature work until:
- Walk-forward testing is in place and passing
- Meaningful paper-trading history exists (weeks of data, not days)

When the time comes, the concept is: use the backtest engine as an evaluation function for
parameter search or LLM-suggested indicator variants. All candidates must pass the same risk
rules as production strategies.

**Not started. No files planned. Revisit in a future session.**

---

## Files Safe to Modify This Session

All Python source files, test files, and docs are safe — the running bot loaded them at startup.
Edits take effect on the next bot restart, not mid-run.

**Config files:** Safe to create new ones or edit configs the bot is NOT currently using.
Do not modify `config/paper_fast.yaml` directly while the bot runs — prefer creating a separate
config file for any experiments (e.g. `config/paper_experiment.yaml`).

## Files That Must NOT Be Modified This Session

| Path | Reason |
|---|---|
| `data/cryptobot.sqlite` | Bot is actively writing journal rows |
| `data/*.parquet` | Bot reads/writes bar cache each poll |
| `logs/` | Bot is writing structured JSON logs |
| `config/paper_fast.yaml` | Currently loaded config — edit only after restart |

---

## Recommended Execution Order

1. `n_bars_held` comment — trivial warmup
2. Windows Unicode fix — 15 minutes, daily dev improvement
3. README/plan.md reconciliation — 10 minutes, clean housekeeping
4. Walk-forward backtest — main work item
5. Phase 8 — future session only
