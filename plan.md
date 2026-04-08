# cryptobot — Development Plan

> Last updated: 2026-04-08  
> Current phase: **Phase 5 — Risk controls + monitoring (in progress)**

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

## Phase 2 — Market data ingestion + persistence ⚠️ PARTIAL

**Goal:** Reliable historical bar fetching and local caching so backtests don't hit the exchange every run.

**Delivered:**
- `exchanges/ccxt_client.py` — `CcxtClient.fetch_ohlcv` (deferred ccxt import, filters incomplete bars, intra-batch gap detection, raises `BarGapError`)
- `exchanges/ccxt_client.py` — `CcxtClient.fetch_ticker`
- `exchanges/base.py` — `ExchangeClient` ABC
- `data/loader.py` — CSV loader for backtesting

**Still TODO:**
- `BarStore.write` / `BarStore.read` — persist bars to parquet, dedupe on `ts_open`, merge with existing file
- CCXT pagination for fetches spanning > 500 bars (exchange limit)
- `cryptobot fetch` CLI command to pre-populate the bar cache

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

## Phase 5 — Risk controls + monitoring 🔄 IN PROGRESS

**Goal:** Make paper trading operationally reliable and observable before considering live trading.

### Pre-phase fixes ✅ All complete (2026-04-08)

| ID | Item | Commit |
|----|------|--------|
| C1 | `PaperBroker.check_stops` — stop-loss monitoring actually fires | `c3ab677` |
| C2 | Market SELL with no holdings → `REJECTED` (not zero-qty fill) | `493be8b` |
| M1 | Rate-limit tracking gated on `FILLED\|ACCEPTED` only | `e099e8e` |
| H1 | `MaxPositionSizePct` + `MaxGrossExposurePct` rules wired | `57c8355` |
| H2 | `BarGapError` on gap in `CcxtClient.fetch_ohlcv` + cross-batch gap in `MarketDataFeed` | `4db39b0` |

### Remaining Phase 5 work

**5a — Telegram notifications**
- [ ] Implement `Notifier.send()` in `monitoring/notify.py` — POST to Telegram Bot API when `bot_token` + `chat_id` are set; no-op (log only) when not configured
- [ ] Wire into `run_paper.py`: notify on trade fill, stop trigger, daily loss limit hit, kill switch, bar gap halt, run start/end
- [ ] Config: `telegram_bot_token` and `telegram_chat_id` already in `EnvSettings`; no YAML changes needed

**5b — Deferred MEDIUM items** *(confirm scope with user before starting)*
- [ ] M3: Synthetic-bar timestamp correction in `MarketDataFeed` (currently uses `bar.ts_open` as fill time, which is the bar open not close)
- [ ] M4: Day-start equity drift — daily PnL resets on calendar day but equity may drift if a position is open over midnight

---

## Phase 6 — Tiny-size live trading (gated) 🔒 NOT STARTED

**Goal:** Place real orders, but with strict size and safety constraints. Only after weeks of validated paper trading.

**Prerequisites before writing a line of Phase 6 code:**
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

## Phase 7 — Analytics + iteration 🔒 NOT STARTED

**Goal:** Make it easy to compare runs, spot regressions, and tune strategy parameters.

**Planned work:**
- `analytics/report.py` — implement `build_report`: read journal, compute per-run PnL, drawdown, Sharpe, win rate, exposure
- `cryptobot report <run_id>` CLI command
- Per-symbol breakdown in reports
- Simple CSV export of fills for external analysis
- Walk-forward backtest utility (run backtest over rolling windows)

---

## Phase 8 — Optional AI-assisted research tools 🔒 NOT STARTED

**Goal:** Use LLM assistance to generate and score strategy variants. Entirely optional.

**Planned work (tentative):**
- Strategy parameter search using the backtest engine as the evaluation function
- LLM-prompted indicator suggestions piped through the existing strategy ABC
- Guardrails: all candidates must pass the same risk rules as production strategies

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

# backtest
cryptobot backtest --config config/backtest.yaml --data path/to/ohlcv.csv

# paper trading
cryptobot paper --config config/paper.yaml

# live (intentionally disabled until Phase 6)
cryptobot live   # raises LiveTradingDisabled
```
