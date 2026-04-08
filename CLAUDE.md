# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependency management uses `uv` (preferred) but plain `pip` works. Python 3.11+ required.

```bash
# install (editable + dev extras)
uv pip install -e ".[dev]"

# run tests
pytest                              # all tests (quiet via pyproject addopts)
pytest tests/test_backtest_engine.py::test_name   # single test

# lint / typecheck
ruff check src tests
mypy src

# CLI
cryptobot version
cryptobot init-db
cryptobot backtest --config config/backtest.yaml --data path/to/ohlcv.csv
cryptobot paper    --config config/paper.yaml
cryptobot live     # intentionally errors out (Phase 6 stub)
```

## Architecture

The system is one synchronous loop (no asyncio, no Kafka, no Redis). Data flows:

```
Config → ExchangeClient → MarketDataFeed → Strategy → RiskManager → Broker → Journal
```

Critical invariants — preserve these when modifying code:

- **Strategies are pure.** `Strategy.on_bar(ctx) -> list[Intent]` may not perform I/O, journal writes, or mutate broker state. Side effects belong in the engine/run-loop layer. See `src/cryptobot/strategy/base.py`.
- **The RiskManager is the only path to a broker.** Every `Intent` must pass through `RiskManager.evaluate()` before becoming an `Order`. Rules short-circuit on first denial. New rules implement `RiskRule` in `src/cryptobot/risk/rules.py`.
- **Brokers share one ABC** (`execution/broker_base.py`) so `BacktestBroker`, `PaperBroker`, and the future `LiveBroker` are swappable. Don't leak broker-specific types upward.
- **Core domain types are immutable dataclasses** in `core/types.py` (`Bar`, `Signal`, `Intent`, `Order`, `Fill`, `Position`). They use `Decimal` for prices/quantities — do not convert to float in storage or arithmetic that affects fills/PnL.
- **No look-ahead in the backtest engine.** Orders submitted on bar `i` settle against bar `i+1`'s open. The bar-loop ordering in `backtest/engine.py` (settle → check stops → mark equity → strategy → risk → submit) is load-bearing; don't reorder casually.

### Configuration layering

`config/settings.py` defines two layers, **env vars beat YAML**:

1. `EnvSettings` (pydantic-settings, loaded from `.env`) — secrets, db url, paths, kill switch path. Never put secrets in YAML.
2. `RunConfig` from `config/*.yaml` — strategy params, symbols, timeframes, risk caps, fees, starting cash, warmup. Validated by pydantic with cross-field rules (e.g. `cooldown_after_losses` requires `cooldown_bars > 0`; `sma_crossover` requires `fast < slow`).

`load_settings(config_path)` returns the combined `Settings`. Pass `None` for env-only (e.g. `cryptobot version`).

### Run-loop layout

The two real entry points are mode-specific run loops in `src/cryptobot/app/`:

- `run_backtest.py` — wires `BacktestBroker` + `BacktestEngine` and streams a CSV.
- `run_paper.py` — wires `PaperBroker` + live `MarketDataFeed` (CCXT) and streams real bars. Tracks daily equity reset, consecutive losses → cooldown, warmup-bars suppression of orders/journal writes, kill-switch file polling, and graceful SIGTERM/Ctrl-C shutdown.
- `run_live.py` — raises `LiveTradingDisabled` by design.

Both run loops construct `RiskState` per bar (equity, gross exposure, daily PnL, orders/min via deque, open positions, consecutive losses) and feed it to `RiskManager.evaluate`. When adding a new rule, plumb the field through `RiskState` in **both** run loops, not just one.

### Journal

`journal/` is SQLAlchemy. `writer.py` exposes `init_db`, `build_engine`, `make_session_factory`, and `record_*` helpers (`record_run_start`, `record_signal`, `record_order`, `record_fill`, `record_run_end`). All writes are tagged with `run_id` (generated via `core/ids.new_run_id`) for forensic queries. The backtest engine itself does **not** write to the journal — that happens in the `app/` run loop layer.

### Safety controls (do not regress these)

- `KillSwitchFile` rule + the run loops both check for the kill-switch file each bar.
- `RequireStopLoss` is enabled by default; BUY intents without `stop_price` are rejected.
- `cryptobot live` must continue to refuse to run until Phase 6.
- Read-only API keys are sufficient for the current phases (data only).

## Git discipline

Commit and push to GitHub regularly throughout any multi-step task — after each logical unit of work, not just at the end. This ensures no progress is lost.

- Write clean, descriptive commit messages in the imperative mood (e.g. `Add MaxOpenPositions risk rule`, `Fix stop-loss fill ordering in BacktestEngine`).
- Push to the remote after every commit: `git push`.
- Never batch unrelated changes into one commit. One concern = one commit.
- Before starting a task, verify the working tree is clean (`git status`). After finishing, verify the push succeeded.

## Repository conventions

- Line length 100, ruff rules `E,F,W,I,B,UP,SIM` (E501 ignored). `from __future__ import annotations` is used throughout.
- Tests live in `tests/` and mirror module names. Use `pytest` directly; there is no separate test runner.
- `data/`, `logs/`, and `.venv/` are gitignored. SQLite lives at `data/cryptobot.sqlite` by default.

## Roadmap context

Phases 1–4 (scaffold, data, backtest, paper) are implemented. Phase 5 (production-grade risk + monitoring) is the next focus — see the auto-memory `pre_phase5_fixes.md` for the open fix queue. Phase 6 (gated tiny-size live trading) is intentionally not started.
