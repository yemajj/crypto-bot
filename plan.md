# cryptobot — Development Plan

> Last updated: 2026-04-08 (session 2)
> Current phase: **Phases 1–5 and Phase 7 complete. Phase 6 (live trading) gated on weeks of paper trading validation.**

---

## Guiding principles

- Safety before profit. No live trading until paper trading is validated for weeks.
- One phase at a time. Don't start the next phase until the current one is solid.
- Keep each phase narrow. Add only what was explicitly planned.
- One synchronous loop. No asyncio, no Kafka, no Redis.

---

## Phase 1 — Scaffold + config + logging ✅ COMPLETE

**Goal:** Establish the project skeleton every later phase builds on.

**Delivered:**
- `core/types.py` — immutable dataclasses: `Bar`, `Signal`, `Intent`, `Order`, `Fill`, `Position`, `BarGapError`
- `config/settings.py` — two-layer config (env vars beat YAML), pydantic-validated `RunConfig` with cross-field rules
- `monitoring/logging_setup.py` — structlog JSON logging with `run_id` threading
- `core/ids.py` — `new_run_id`, `new_order_id`
- `execution/broker_base.py` — `Broker` ABC shared by all three broker implementations
- `execution/fees.py` — `FeeModel` (taker/maker bps + slippage bps)
- `risk/rules.py` — `RiskRule` ABC + initial rules: `SymbolAllowList`, `MaxOrdersPerMinute`, `RequireStopLoss`, `MaxDailyLoss`, `KillSwitchFile`, `OneOrderPerSymbolInFlight`, `MaxOpenPositions`, `CooldownAfterLoss`, `MaxPositionSizePct`, `MaxGrossExposurePct`
- `risk/manager.py` — `RiskManager.evaluate(intent, state)` short-circuits on first denial
- `journal/models.py` + `journal/writer.py` — SQLAlchemy ORM, `record_run_start/signal/order/fill/run_end`
- `cli.py` — Typer CLI: `version`, `init-db`, `backtest`, `paper`, `live` (live intentionally errors)
- Stubs: `data/storage.py` (`BarStore`), `monitoring/notify.py` (`Notifier`), `analytics/report.py` (`build_report`)

---

## Phase 2 — Market data ingestion + persistence ✅ COMPLETE

**Goal:** Reliable historical bar fetching and local caching so backtests don't hit the exchange every run.

**Delivered:**
- `exchanges/ccxt_client.py` — `CcxtClient.fetch_ohlcv` (deferred ccxt import, filters incomplete bars, intra-batch gap detection, raises `BarGapError`; supports `since=` for pagination)
- `exchanges/ccxt_client.py` — `CcxtClient.fetch_ticker`
- `exchanges/base.py` — `ExchangeClient` ABC
- `data/storage.py` — `BarStore`: parquet-backed cache, atomic writes via `os.replace()`, dedup on `ts_open`, sorted ascending; `read()` returns `list[Bar]`
- `data/loader.py` — `HistoricalLoader.fetch()`: paginated CCXT fetch → BarStore, gap-skip-and-continue, `until` boundary, idempotent (dedup handled by BarStore); injectable `batch_size` for testing
- `data/feed.py` — `MarketDataFeed` warm-start: reads cache, fetches only gap bars from exchange on restart
- `cli.py` — `cryptobot fetch-history --symbol --timeframe --since [--until]`

---

## Phase 3 — Strategy engine + backtest ✅ COMPLETE

**Goal:** A deterministic bar-by-bar backtest engine with no look-ahead bias.

**Delivered:**
- `strategy/base.py` — `Strategy` ABC (`on_bar(ctx) -> list[Intent]`) + `StrategyContext`
- `strategy/sma_crossover.py` — SMA(20,50) crossover with ATR-based stop-loss sizing
- `strategy/registry.py` — `get_strategy(name)` factory
- `execution/backtest_broker.py` — `BacktestBroker`: orders submitted on bar N fill at bar N+1 open; `check_stops(bar)` fires on `bar.low <= stop_price`; `register_stop` / `clear_stop`
- `backtest/engine.py` — bar loop ordering: settle fills → check stops → mark equity → strategy → risk → submit (load-bearing; do not reorder)
- `backtest/metrics.py` — total return, max drawdown, Sharpe, win rate
- `app/run_backtest.py` — end-to-end backtest entry point with journal writes
- `data/loader.py` — CSV → `list[Bar]` loader

---

## Phase 4 — Paper trading ✅ COMPLETE

**Goal:** Run the strategy live against real market data without placing real orders.

**Delivered:**
- `exchanges/ccxt_client.py` — live OHLCV polling + ticker
- `data/feed.py` — `MarketDataFeed.stream()`: warmup phase then live polling with deduplication and cross-batch gap detection
- `execution/paper_broker.py` — `PaperBroker`: market fills at close ± slippage; limit order simulation; cash guard; `check_stops(bar)` for stop-loss monitoring; `register_stop` / `clear_stop`
- `app/run_paper.py` — full run loop: settle limits → check stops → mark price → daily equity reset → cooldown countdown → strategy → risk → submit; kill-switch polling; SIGTERM handler; `BarGapError` halt; warmup suppression of orders/journal writes
- Risk rules wired: all Phase 1 rules active in paper mode, `mark_price_by_symbol` in `RiskState`
- Journal: all events tagged with `run_id`

---

## Phase 5 — Risk controls + monitoring ✅ COMPLETE

**Goal:** Make paper trading operationally reliable and observable before considering live trading.

**Delivered:**
- All pre-phase fixes (C1–H2) shipped 2026-04-08
- `monitoring/notify.py` — `Notifier`: real Telegram HTTP via `urllib`, no-op when unconfigured, swallows network errors
- `run_paper.py` — notifications on: run start/end, every fill (BUY/SELL), stop-loss trigger, daily loss cap denial
- `journal/models.py` — `EquitySnapshotRow` table (run_id, bar_ts, equity, cash)
- `journal/writer.py` — `record_equity_snapshot()` (paper, one row/bar) + `record_equity_snapshots_bulk()` (backtest, one transaction)
- `analytics/queries.py` — DB query layer: `get_equity_curve()`, `reconstruct_trades()` (FIFO), `daily_summary()`, `symbol_breakdown()`, `fee_impact()`
- `analytics/report.py` — full text report using DB equity snapshots for accurate Sharpe/drawdown; pre-live checklist (Sharpe > 1.0, DD < 20%, ≥30 trades, win rate > 40%, fees < 15% gross)
- `analytics/validation.py` — multi-run validation summary for completed tagged paper runs
- `cli.py` — `cryptobot report [--run-id] [--list]` and `cryptobot validate-paper [--days N] [--run-id ...]`
- `config/paper_validation.yaml` + `docs/paper_validation.md` — standard validation profile and runbook

---

## Phase 6 — Tiny-size live trading (gated) 🔒 NOT STARTED

**Goal:** Place real orders, but with strict size and safety constraints. Only after weeks of validated paper trading.

**Validation tooling already shipped:**
- `analytics/report.py` — per-run report with quantitative pre-live checklist
- `analytics/validation.py` — multi-run validation summary for completed tagged paper runs
- `cli.py` — `cryptobot report` and `cryptobot validate-paper`
- `config/paper_validation.yaml` — standard long-running paper-validation profile
- `docs/paper_validation.md` — runbook for manual and automated validation

**Manual prerequisites before writing a line of Phase 6 code:**
- [ ] Weeks of paper trading logs reviewed — no systematic bugs
- [ ] Paper PnL matches backtest expectations within reasonable bounds
- [ ] Kill switch and daily loss limits manually tested
- [ ] Telegram alerts verified reliable

**Planned work:**
- `execution/live_broker.py` — `LiveBroker` implementing the `Broker` ABC via CCXT order placement
- Partial fill handling (paper assumes all-or-nothing)
- Exchange minimum order size + precision enforcement
- Order reconciliation on restart (re-fetch open orders from exchange)
- `app/run_live.py` — replaces the current stub that raises `LiveTradingDisabled`
- Notional cap: hard-coded maximum position size (e.g. $50) enforced in `LiveBroker`, separate from percentage-based risk rules
- Read/write API keys required (currently read-only is sufficient)

---

## Phase 7 — Ensemble strategy system ✅ COMPLETE

**Goal:** Add RSI, Donchian, and Bollinger strategies plus a weighted ensemble that composes
them with regime-aware scoring — while leaving the existing SMA crossover and all run loops
completely unchanged.

**Design decisions (locked):**
- `sma_crossover.py` is not modified. The ensemble maps its `on_bar` intents to ±1.0 scores.
- Volume is a confidence multiplier (±25% adjustment to final score), not a directional bucket.
- 4 directional buckets: trend, momentum, breakout, volatility.
- Agreement filter: a bucket counts only if `abs(score) ≥ 0.25` and direction matches.
- Bollinger is expansion-confirmation in v1 (silent during compression); designed for future squeeze-detection upgrade.
- Regime weights re-normalize dynamically across only configured buckets.
- `stop_distance_multiplier` is configurable (default 1.5).

**New files:**

| File | Purpose |
|---|---|
| `strategy/base.py` | Add `ScoringStrategy` ABC + `_compute_atr` helper |
| `strategy/rsi.py` | RSI momentum scorer (bucket: momentum) |
| `strategy/donchian.py` | Donchian breakout scorer (bucket: breakout) |
| `strategy/bollinger.py` | Bollinger expansion-confirmation scorer (bucket: volatility) |
| `strategy/volume_signal.py` | Volume confidence multiplier (not a directional bucket) |
| `strategy/regime_detector.py` | Pure `detect_regime(bars) -> Regime` (trending/ranging/breakout_watch) |
| `strategy/signal_aggregator.py` | 4-bucket weighted aggregator + volume multiplier + agreement filter |
| `strategy/ensemble.py` | `EnsembleStrategy` registered as `"ensemble"` |
| `config/ensemble.yaml` | Runnable ensemble config (paper mode, BTC/USDT 1h) |
| `tests/test_rsi.py` | RSI scorer tests |
| `tests/test_donchian.py` | Donchian scorer tests |
| `tests/test_bollinger.py` | Bollinger scorer tests |
| `tests/test_volume_signal.py` | Volume multiplier tests |
| `tests/test_regime_detector.py` | Regime detection tests |
| `tests/test_signal_aggregator.py` | Aggregation, weighting, agreement filter tests |
| `tests/test_ensemble.py` | End-to-end ensemble tests incl. SMA mapping and params isolation |

**Backward compatibility guarantees:**
- `cryptobot backtest --config config/backtest.yaml` — unchanged
- `cryptobot paper --config config/paper.yaml` — unchanged
- All existing tests pass without modification
- `get_strategy("sma_crossover")` continues to work; new names added alongside

**Ensemble architecture:**

```
Strategy (ABC)
  └─ ScoringStrategy (ABC)    ← new in base.py
       ├─ RsiStrategy          ← new
       ├─ DonchianStrategy     ← new
       ├─ BollingerStrategy    ← new
       └─ VolumeSignalStrategy ← new (used as multiplier, not bucket)

SmaCrossover                   ← UNCHANGED
EnsembleStrategy(Strategy)     ← new; wraps sub-strategies via config
```

**Also delivered after ensemble:**
- Walk-forward backtest utility: split data into in-sample / out-of-sample windows, run backtest over each, compare metrics via `cryptobot walk-forward`

---

## Phase 8 — Optional AI-assisted research tools 🔒 NOT STARTED

**Goal:** Use LLM assistance to generate and score strategy variants. Entirely optional.

**Planned work (tentative):**
- Strategy parameter search using the backtest engine as the evaluation function
- LLM-prompted indicator suggestions piped through the existing strategy ABC
- Guardrails: all candidates must pass the same risk rules as production strategies

---

## Maintenance — Quick-win bug fixes ✅ COMPLETE (2026-04-08 session 2)

Six correctness issues identified via code review and shipped in one commit:

- **metrics.py** — break-even trades (pnl == 0) no longer counted as losses in `profit_factor` / `hit_rate`
- **run_paper.py** — `signal.signal(SIGTERM, ...)` wrapped in try/except for Windows compatibility
- **rules.py** — `MaxOpenPositions` now allows adding to an already-open position (pyramiding was incorrectly blocked)
- **paper_broker.py** — added `mark_prices()` public accessor; removed direct `_mark_prices` access from run loop
- **paper_broker.py** — `equity()` now raises `RuntimeError` instead of silently falling back when no mark price is set
- **risk/state_builder.py** (new) — `build_risk_state()` shared helper extracted from both `run_paper.py` and `backtest/engine.py` to prevent future drift

---

## Explicitly deferred / out of scope

These were identified in the 2026-04-07 design review and deliberately not implemented:

- **L1** Duplicate slippage calc in `PaperBroker._submit_market` (cosmetic, not a bug)
- **L2** Time-capped fill deque (memory bounded by `_MAX_FILL_HISTORY` already)
- **L3/M2** Clock injection into `PaperBroker` (testability improvement, not correctness)
- **L4** Equity invariant assertion
- **L5** Backtest sell clamp (slippage on clamped qty)
- **L6** Portfolio-aware `StrategyContext` (multi-symbol positions visible to strategy)
- **M5** Async journal writes
- **H3** Warmup retry on network error

Phase-6 live prerequisites (async fills, exchange precision, reconciliation) are not in scope until Phase 6 is explicitly started.

---

## How to run

```bash
# install
uv pip install -e ".[dev]"

# tests
pytest

# pre-populate bar cache (run once before backtest/paper)
cryptobot fetch-history --symbol BTC/USDT --timeframe 1h --since 2024-01-01

# backtest (against cached bars or a CSV)
cryptobot backtest --config config/backtest.yaml --data path/to/ohlcv.csv

# paper trading
cryptobot paper --config config/paper.yaml

# paper validation summary
cryptobot validate-paper

# performance report
cryptobot report                         # latest run
cryptobot report --run-id bt_abc123     # specific run
cryptobot report --list                 # all runs

# live (intentionally disabled until Phase 6)
cryptobot live   # raises LiveTradingDisabled
```
