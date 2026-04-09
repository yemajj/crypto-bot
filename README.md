# cryptobot

A safety-first, modular crypto trading framework for a solo builder.

> **Status: Phases 1–5 and 7 complete.** Backtesting, paper trading, analytics reporting,
> Telegram notifications, bar caching, historical data ingestion, and an ensemble strategy
> system (RSI, Donchian, Bollinger, regime-aware weighted scoring) are fully implemented.
> Live trading is intentionally **not** implemented and will only be introduced after weeks
> of validated paper trading, with tiny notional caps. This is **not** a money printer.

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
  core/          Shared types, clock abstraction, id generation.
  monitoring/    structlog setup, optional notifications.
  exchanges/     ExchangeClient ABC + ccxt-based implementation.
  data/          OHLCV feed, historical loader, SQLite + parquet storage.
  strategy/      Strategy ABC, registry, starter SMA crossover (Phase 3).
  risk/          Risk rules and gatekeeper.
  execution/     Broker ABC, paper / backtest brokers, fees + slippage.
  backtest/      Bar-by-bar engine, metrics, walk-forward validation.
  journal/       SQLAlchemy models + writer (runs, signals, orders, fills).
  analytics/     Per-run reporting and multi-run paper-validation summaries.
  app/           Mode entry points (run_backtest, run_paper, run_live).
  cli.py         Typer CLI (`cryptobot ...`).
config/          YAML config files (default / backtest / paper / validation).
tests/           Pytest tests for foundational pieces.
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

## Roadmap

| Phase | Goal                                | Status |
|-------|-------------------------------------|--------|
| 1     | Scaffold + config + logging         | **complete** |
| 2     | Market data ingestion + persistence | **complete** (BarStore parquet cache, gap detection, paginated `fetch-history`) |
| 3     | Strategy engine + backtest          | **complete** |
| 4     | Paper trading                       | **complete** |
| 5     | Risk controls + monitoring          | **complete** (Telegram alerts, equity snapshots, analytics reporting, pre-live checklist) |
| 6     | Tiny-size live trading (gated)      | TODO   |
| 7     | Ensemble strategy system            | **complete** (RSI, Donchian, Bollinger, volume multiplier, regime detector, weighted ensemble) |
| 8     | Optional AI-assisted research tools | TODO   |

---

## Paper validation

Use `config/paper_validation.yaml` for long-running validation runs that count toward the Phase 6 gate.
`config/paper_fast.yaml` is for development only and should not be treated as live-readiness evidence.

```bash
# preload validation data
cryptobot fetch-history --symbol BTC/USDT --timeframe 5m --since 2024-01-01

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

## License

MIT.
