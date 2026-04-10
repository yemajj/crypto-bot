# cryptobot

A safety-first, modular crypto trading framework for a solo builder.

> **Status: Phases 1–5, 7, and Architecture Review (Phase 8) complete.** Backtesting, paper
> trading, analytics reporting, Telegram notifications, bar caching, historical data ingestion,
> ensemble strategy system, walk-forward parameter grid search, allocation layer, richer metrics
> (Sortino, Calmar, consecutive streaks), size-tiered slippage, and gap-through stop handling
> are fully implemented. Live trading is intentionally **not** implemented and will only be
> introduced after weeks of validated paper trading, with tiny notional caps. This is **not** a
> money printer.

---

## Philosophy

1. Safety before profit.
2. Simplicity before complexity.
3. Validation before automation.
4. Modular architecture before feature sprawl.
5. Observability/logging before live trading.
6. Realistic execution assumptions before performance claims.
7. Long-term maintainability over short-term cleverness.

Assume no strategy has edge until proven with realistic fees, slippage, and
out-of-sample paper trading.

---

## Architecture overview

```
Config -> Exchange Client -> Market Data Feed -> Storage
                                   |
                                   v
                            Strategy Engine
                                   |
                                   v
                             Risk Manager  (gatekeeper, kill switch)
                                   |
                                   v
                          Execution Router
                  (BacktestBroker | PaperBroker | LiveBroker*)
                                   |
                                   v
                            Trade Journal
                                   |
                                   v
                       Monitoring / Logging / Alerts

* LiveBroker is intentionally a stub in v1.
```

Key principles:

- One synchronous loop in v1. No asyncio, no Kafka, no Redis.
- Strategies are **pure**: `on_bar(context) -> list[Intent]`. No side effects.
- The **risk manager** is the only path to the broker. It can veto any order.
- All brokers share one interface, so backtest / paper / live are swappable.
- Every signal, intent, risk decision, order, and fill is logged with a
  `run_id` for forensic analysis.

---

## Project layout

```
src/cryptobot/
  config/        Layered settings (env + YAML), pydantic-validated.
  core/          Shared types, clock abstraction, id generation, Regime enum.
  monitoring/    structlog setup, optional notifications.
  exchanges/     ExchangeClient ABC + ccxt-based implementation.
  data/          OHLCV feed, historical loader, SQLite + parquet storage.
  strategy/      Strategy ABC, registry, SMA/RSI/Donchian/Bollinger/Ensemble, regime detector.
  allocation/    Allocator ABC, FixedRiskAllocator, RegimeScaledAllocator.
  risk/          Risk rules and gatekeeper (10 composable rules).
  execution/     Broker ABC, paper / backtest brokers, size-tiered fee/slippage model.
  backtest/      Bar-by-bar engine, metrics (Sharpe/Sortino/Calmar/streaks), walk-forward
                 with optional per-fold parameter grid search.
  journal/       SQLAlchemy models + writer (runs, signals, orders, fills, equity snapshots).
  analytics/     Per-run reporting and multi-run paper-validation summaries.
  app/           Mode entry points (run_backtest, run_paper, run_live).
  services/      UI-agnostic service layer (run lifecycle, log reader, config writer).
  dashboard/     Streamlit dashboard (app.py + 6 pages).
  cli.py         Typer CLI (`cryptobot ...`).
config/          YAML config files (default / backtest / paper / validation).
tests/           285 pytest tests (metrics, allocator, param grid, broker, risk, walk-forward...).
data/            Local SQLite + parquet (gitignored).
logs/            Structured JSON logs (gitignored).
```

---

## Quick start

Requires **Python 3.11+**. [`uv`](https://github.com/astral-sh/uv) recommended,
but plain `pip` also works.

```bash
# 1. clone & enter
cd crypto-bot

# 2. create env + install
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# (or with pip)
# python -m venv .venv && source .venv/bin/activate
# pip install -e ".[dev]"

# 3. copy env file and edit
cp .env.example .env

# 4. sanity-check the CLI
cryptobot --help
cryptobot version

# 5. run tests
pytest
```

---

## Configuration

Two layers, in order of precedence (highest first):

1. **Environment variables** (and `.env`) — secrets, runtime mode, paths.
   Loaded via `pydantic-settings`. See `.env.example`.
2. **YAML files** under `config/` — strategy params, symbols, timeframes,
   risk caps. Pick a file with `--config config/backtest.yaml`.

Secrets (`EXCHANGE_API_KEY`, etc.) live **only** in `.env`. They are never
written to YAML or to logs.

---

## Safety controls

- **Read-only API keys** are sufficient for v1 (data only).
- **Kill switch**: create the file at `CRYPTOBOT_KILL_SWITCH_FILE` (default
  `./KILL_SWITCH`) and any running loop halts within one bar.
- **Risk manager** enforces hard caps: max position size, max gross exposure,
  max daily loss, orders/minute, symbol allow-list.
- **No live trading in v1.** `cryptobot live` is a stub that refuses to run.

---

## Dashboard

A Streamlit UI lets you operate the bot — start/stop paper runs, launch
backtests, inspect results, and toggle the kill switch — without touching the
terminal.

### Launch

```bash
cryptobot dashboard
# or directly:
streamlit run src/cryptobot/dashboard/app.py
```

The dashboard opens in your browser at `http://localhost:8501`.

### Pages

| Page | What it does |
|------|-------------|
| **Home** | Live run-status card (equity, run ID) and kill-switch toggle. Auto-refreshes every 5 s. |
| **Paper Trading** | Start/stop a paper run, select config from sidebar, watch the equity curve update in real time. |
| **Backtest** | Pick a config + OHLCV CSV path, run a backtest, and view the equity curve, metrics summary (Sharpe, Sortino, Calmar, drawdown, hit rate, consecutive losses), trades table, daily PnL, and symbol breakdown inline. |
| **Config Editor** | Read-only display of the selected YAML config (v1). A safe-edit form (writes to `*.custom.yaml`, never overwrites the source) will ship as a follow-on. |
| **Logs** | Tail structured log lines for any run; filter by level and number of lines. Auto-refreshes every 5 s. |
| **History** | Searchable list of all runs with drill-down: equity curve, trades, daily PnL, symbol/strategy breakdowns, fee-impact summary, and the full pre-live report. |

### Notes

- **One active run at a time.** The dashboard enforces a single paper run or
  backtest; multi-run management is out of scope for v1.
- **Kill switch** can be armed/disarmed from the Home page. The running loop
  checks the file every bar and halts within seconds.
- **Config editing is read-only in v1.** Edit YAML files directly or use the
  CLI. When editing ships, changes will be saved to `<name>.custom.yaml` and
  secrets (`EnvSettings`) will never be exposed in the UI.
- The paper run survives Streamlit page refreshes — the background thread is a
  module-level singleton.

---

## Roadmap

| Phase | Goal                                | Status |
|-------|-------------------------------------|--------|
| 1     | Scaffold + config + logging         | **complete** |
| 2     | Market data ingestion + persistence | **complete** (BarStore parquet cache, gap detection, paginated `fetch-history`) |
| 3     | Strategy engine + backtest          | **complete** |
| 4     | Paper trading                       | **complete** |
| 5     | Risk controls + monitoring          | **complete** (Telegram alerts, equity snapshots, analytics reporting, pre-live checklist) |
| 7     | Ensemble strategy system            | **complete** (RSI, Donchian, Bollinger, volume multiplier, regime detector, weighted ensemble) |
| 8     | Architecture review + evaluation layer | **complete** (Sortino/Calmar/streak metrics, walk-forward grid search, allocation layer, size-tiered slippage, gap-through stops, 285 tests) |
| 9     | Strategy evaluation + walk-forward runs | **next** — run the improved walk-forward on real data, evaluate out-of-sample results; if strategy consistently loses OOS, consider P3 regime improvements (ADX + volatility percentile) |
| 6     | Tiny-size live trading (gated)      | blocked until Phase 9 pre-live checklist gates pass |

---

## Paper validation

Use `config/paper_validation.yaml` for long-running validation runs that count toward the Phase 6 gate.
`config/paper_fast.yaml` is for development only and should not be treated as live-readiness evidence.

```bash
# preload validation data
cryptobot fetch-history --symbol BTC/USDT --timeframe 5m --since 2024-01-01

# (optional) export cached bars to CSV for backtesting against the same data
cryptobot export-history --symbol BTC/USDT --timeframe 5m
cryptobot backtest --config config/backtest_paper_5m.yaml --data data/BTC_USDT_5m.csv

# start a validation run
cryptobot paper --config config/paper_validation.yaml

# inspect the latest run
cryptobot report

# inspect readiness across tagged validation runs
cryptobot validate-paper
cryptobot validate-paper --days 28
cryptobot validate-paper --run-id paper_abc123 --run-id paper_def456
```

Stop a paper run with `Ctrl-C` or by creating the kill-switch file. For the full runbook and manual checks, see `docs/paper_validation.md`.

---

## Walk-forward backtesting

Walk-forward validation is the primary tool for detecting overfitting before paper trading.

```bash
# Fixed params (from YAML config) — baseline check
cryptobot walk-forward --config config/backtest.yaml --data data/BTC_USDT_1h.csv --folds 5

# Per-fold parameter grid search — finds best in-sample params, validates OOS
cryptobot walk-forward \
  --config config/backtest.yaml \
  --data data/BTC_USDT_1h.csv \
  --folds 5 \
  --optimise \
  --param-grid '{"fast": [10, 15, 20, 25], "slow": [40, 50, 60]}'
```

The output reports Sharpe, Sortino, Calmar, MaxDD, win rate, and trade count for each in-sample / out-of-sample fold, plus a summary row across all OOS folds.

**Interpretation guide:**
- If OOS Sharpe is consistently < 0: the strategy has no edge at these params. Tune further before paper trading.
- If OOS Sharpe > 0 but << in-sample Sharpe: likely overfitting. Try a smaller param grid or fewer params.
- If OOS and in-sample Sharpe are similar: the strategy generalises. Proceed to paper trading.
- Max consecutive losses > 5 OOS: review position sizing — a run of losses may exceed daily loss cap.

---

## License

MIT.
