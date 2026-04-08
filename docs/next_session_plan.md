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

**Scope — new files only, zero impact on running bot:**

| File | Purpose |
|---|---|
| `src/cryptobot/backtest/walk_forward.py` | `WalkForwardEngine`: slices bars into folds, runs `BacktestEngine` on each, returns `list[FoldResult]` |
| New CLI command in `src/cryptobot/cli.py` | `cryptobot walk-forward --config ... --data ... --folds N [--in-sample-pct 0.7]` |

**Reuse (do not rewrite):**
- `src/cryptobot/backtest/engine.py` — existing `BacktestEngine`, bar-loop logic unchanged
- `src/cryptobot/backtest/metrics.py` — existing `BacktestResult`, returned per fold
- `src/cryptobot/app/run_backtest.py` — reference for how engine is wired

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
- More surgical — keeps the symbols, fixes only the encoding.
- Slightly more complex; needs the `TextIOWrapper` approach to work correctly with Typer.

**Verification:** Run `cryptobot report` on Windows without `PYTHONIOENCODING=utf-8`. Should print without crashing.

---

### 3. `n_bars_held` Comment (trivial)

**Priority: Low** — one-line comment, no logic change.

**File:** `src/cryptobot/analytics/queries.py`, `reconstruct_trades()`.

**Issue:** `n_bars_held=0` is hardcoded because the DB schema records fills (not bar entry/exit
timestamps) and reconstructing bar count from timestamps requires knowing the timeframe, which
the analytics layer doesn't currently receive. This is not a bug — PnL is correct — but a
future reader will be confused.

**Fix:** Add a comment:
```python
n_bars_held=0,  # TODO: derive from fill timestamps + timeframe once timeframe is in journal
```

---

### 4. Phase 8 — AI-Assisted Research Tools (optional, low priority)

**Priority: Low / Optional** — defer until paper trading accumulates meaningful data.

**Concept:**
- Strategy parameter search: use the backtest engine as an evaluation function, sweep params
  (e.g. RSI window, Donchian window), rank by Sharpe on out-of-sample window.
- LLM-prompted indicator suggestions: describe a market condition → LLM proposes indicator
  thresholds → auto-run as a backtest → report results.
- All candidates must pass the same risk rules as production strategies.

**Not started. No files planned yet.**

---

## Files Safe to Modify This Session

All Python source files, test files, config files, and docs are safe — the running bot
loaded them at startup. Edits will take effect on the next bot restart, not mid-run.

## Files That Must NOT Be Modified This Session

| Path | Reason |
|---|---|
| `data/cryptobot.sqlite` | Bot is actively writing journal rows |
| `data/*.parquet` | Bot reads/writes bar cache each poll |
| `logs/` | Bot is writing structured JSON logs |

---

## Recommended Execution Order

1. `n_bars_held` comment — do it in 2 minutes as a warmup
2. Windows Unicode fix — 15 minutes, improves daily dev experience
3. Walk-forward backtest — main work item, 1–2 hours
4. Phase 8 — defer to a future session after paper data is available

---

## Architecture Notes for Walk-Forward Implementation

The existing backtest engine bar-loop ordering is **load-bearing** (from `CLAUDE.md`):
```
settle fills → check stops → mark equity → strategy → risk → submit
```
Do not reorder when slicing bars for walk-forward. Each fold should construct a fresh
`BacktestEngine`, `BacktestBroker`, and `RiskManager` instance — no shared state between folds.

The `WalkForwardEngine` should accept the same inputs as `run_backtest`:
- `bars: list[Bar]` — full historical series
- `settings: Settings` — strategy params, risk caps, fees
- `folds: int` — number of folds (default 5)
- `in_sample_pct: float` — fraction of each fold used for training (default 0.7)

Return type: `list[FoldResult]` where `FoldResult` holds `(in_sample: BacktestResult, out_sample: BacktestResult, fold_bars_in: list[Bar], fold_bars_out: list[Bar])`.
