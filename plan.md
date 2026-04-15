# cryptobot — Development Plan

> Last updated: 2026-04-09 (session 3)
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

## Session 3 additions (2026-04-09)

**Historical replay workflow:**
- `cli.py` — `cryptobot export-history` exports cached parquet OHLCV to a backtest-ready CSV
- `config/backtest_paper_5m.yaml` — backtest config aligned with paper.yaml (BTC/USDT 5m)
- `app/run_backtest.py` — now reads `starting_cash` from config YAML instead of hardcoded 10000

**Automated paper trading CI:**
- `.github/workflows/paper-trade.yml` — GitHub Actions workflow; dispatch-triggered; fetches 5m history, runs paper session, uploads logs + SQLite as artifacts
- `config/paper_fast_multi.yaml` — multi-symbol stress test config (BTC/USDT, ETH/USDT, SOL/USDT)
- `docs/paper_trading_activity_recommendations.md` — ranked recommendations to increase trade volume for faster iteration

**Bug fixes:**
- `strategy/sma_crossover.py` — added `max_position_notional_pct` param (default 1.0 = no cap) to prevent ATR sizing from producing oversized positions on high-price assets; validated in `StrategyConfig`
- `app/run_paper.py` — fixed `warmup_remaining` dict comparison that caused `TypeError` on log line after per-symbol refactor

**Testing:**
- `config/paper_fast_open.yaml` — testing-only config: SMA(5,15) + loosened circuit breakers; explicit documentation that it is not for production validation
- `tests/test_sma_crossover.py` — two new tests covering `max_position_notional_pct` cap behavior

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

# Current Direction Update

## Summary of recent findings

Recent strategy validation suggests the current framework should be kept, but the main day-trading strategy direction needs to change.

### What has been learned
- RSI mean reversion on BTC is a poor fit and should not be the main day-trading path
- SMA crossover / current ensemble logic may still have some value as a lower-frequency swing-mode candidate, but not for the goal of a couple trades per day
- Raw Donchian breakout on BTC 15m is directionally more sensible than RSI, but still has no edge in its current form
- Donchian + ADX confirmation improved results versus raw Donchian, but still failed materially and should not be expanded further as the main day-trading path
- ORB on 1h BTC failed: -5.4% total return, Sharpe -1.16, 26% win rate (624 trades). Too coarse — signal arrives 1 bar late
- ORB on 5m BTC failed worse: -21.2% total return, Sharpe -6.06, 9.3% win rate (1826 trades). Too granular — noise dominates
- Day-mode research is parked. ORB has no edge on BTC at either timeframe tested
- Multi-symbol 4h SMA swing basket (BTC+ETH+SOL) produced portfolio Sharpe 0.94 — best result so far
- Walk-forward OOS is inconclusive due to too few trades per fold (1-4); needs broader basket to get meaningful OOS statistics

## Core decision

Do not restart the bot from scratch.

Keep the current framework and continue iterating forward. The repo architecture remains useful. The problem is primarily in the strategy layer and validation results, not in the existence of the framework itself.

## Strategy tracks going forward

### Day mode
Primary research focus is now a separate day-trading track.

Current next candidate:
- Opening Range Breakout (ORB) on 5m bars

Reason:
- ORB is a short-term intraday strategy by design; 1h bars are too coarse (signal arrives 1 bar late)
- 5m bars give the strategy its intended resolution: 12-bar opening range = 1 hour, entry cutoff at bar 192 = 16:00 UTC
- structurally different from the failed single-indicator tests
- more tied to session behavior and market structure than simple threshold-based indicator triggers

Status:
- ORB 1h BTC: FAILED (-5.4% return, Sharpe -1.16, 26% win rate) — timeframe too coarse
- ORB 5m BTC: IN PROGRESS — config at `config/backtest_orb_5m.yaml`, data at `data/BTC_USDT_5m_full.csv`

### Swing mode
Keep 4h SMA as a parked swing-mode candidate.

Reason:
- it may still be useful as a lower-frequency overlay or separate mode
- it does not fit the current day-trading frequency goal
- it should not be the main research focus right now

## Multi-symbol direction

Multi-symbol expansion still makes sense in principle, but only after a strategy family shows credible edge on at least one symbol first.

Do not use multi-symbol testing to rescue a strategy that is clearly losing on single-symbol validation.

The intended order is:
1. validate a promising strategy family on one symbol
2. expand to a small basket
3. evaluate portfolio-level trade frequency, drawdown, and symbol concentration
4. only then consider broader deployment

## Framework guidance

Continue using the existing bot architecture.

Do not copy an external framework wholesale.
Do not add advanced complexity just because it sounds more sophisticated.
Prioritize:
- cleaner strategy logic
- realistic validation
- mode separation
- reproducible research workflows
- disciplined iteration

## Immediate next step

Multi-symbol swing-mode validation is now the active research path. Results so far:

**Full-period basket backtest (SMA 20/50 4h, BTC+ETH+SOL):**
- Portfolio Sharpe: 0.94 — best result produced in this project
- Avg return: +5.0% equal-weight
- Portfolio max drawdown: -2.0%
- Per-symbol: BTC +5.5% (Sharpe 1.07), ETH -2.6% (Sharpe -0.68), SOL +12.1% (Sharpe 0.89)
- Avg pairwise correlation: 0.04 (signals fire independently)
- Dominant symbol: SOL (69% of positive PnL)

**Walk-forward OOS (5 folds):**
- BTC OOS mean Sharpe: +0.06 (3 total OOS trades — uninterpretable)
- ETH OOS mean Sharpe: -1.74 (7 OOS trades)
- SOL OOS mean Sharpe: -0.98 (6 OOS trades)
- Root cause: too few trades per fold (1-4) for statistics to be meaningful

**Verdict: mixed (inconclusive)** — full-period signal is the strongest we've seen, but trade
sparsity (0.044 trades/day across 3 symbols) makes walk-forward uninterpretable. ETH
is a consistent drag. SOL dominates gains.

**Next validation steps (in priority order):**
1. Expand basket to 5-6 symbols to increase trade frequency without changing strategy
2. Drop ETH or replace with a better-performing alt (SOL, BNB, AVAX)
3. Once basket produces > 20 OOS trades per fold, re-run walk-forward for a real read
4. If OOS Sharpe holds > 0.5: proceed to paper trading this basket
5. If OOS still negative at adequate trade count: abandon SMA family; try a genuinely different class

```bash
# Run multi-symbol basket backtest
cryptobot multi-backtest --config config/backtest_swing_basket.yaml --data-dir data/ --out-dir results/swing_basket

# Per-symbol walk-forward
cryptobot walk-forward --config config/backtest_swing_basket.yaml --data data/BTC_USDT_4h.csv --folds 5
```

Keep the GitHub Actions research workflow as the standard path for:
- fetching/exporting real historical data
- running backtests
- reviewing verdict-first summaries remotely

## Decision guardrails

- no synthetic data for strategy validation
- no curve fitting to rescue weak strategies
- no multi-symbol scaling until single-symbol edge is credible
- no restart unless the framework itself becomes the bottleneck

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
