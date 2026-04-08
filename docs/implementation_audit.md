# Implementation Audit — 2026-04-08

> Reference document. Covers phases 1–5. Generated from a full codebase inspection.

---

## Files by functional area

### 1. Backtesting

| File | Role |
|---|---|
| `src/cryptobot/backtest/engine.py` | Bar-by-bar loop (settle → stops → equity → strategy → risk → submit) |
| `src/cryptobot/backtest/metrics.py` | total_return, max_drawdown, Sharpe, hit_rate, profit_factor |
| `src/cryptobot/execution/backtest_broker.py` | Fills at next bar open; stop-loss fires on `bar.low ≤ stop_price` |
| `src/cryptobot/app/run_backtest.py` | Entry point; wires CSV loader → engine → journal writes |

### 2. Paper trading

| File | Role |
|---|---|
| `src/cryptobot/execution/paper_broker.py` | Market fills at mark ± slippage; limit simulation; stop-loss checks |
| `src/cryptobot/app/run_paper.py` | Full run loop; SIGTERM handler; kill-switch polling; warmup suppression |
| `src/cryptobot/data/feed.py` | Warm-start from cache + live CCXT polling; gap detection |
| `src/cryptobot/exchanges/ccxt_client.py` | CCXT wrapper; `fetch_ohlcv`, `fetch_ticker` |

### 3. Risk controls & monitoring

| File | Role |
|---|---|
| `src/cryptobot/risk/rules.py` | 10 rules (see below) |
| `src/cryptobot/risk/manager.py` | `evaluate(intent, state)` — short-circuits on first denial |
| `src/cryptobot/monitoring/logging_setup.py` | structlog JSON logging with `run_id` threading |

**Active risk rules (all implemented, none stubbed):**
1. `SymbolAllowList` — Denies trades outside a configured whitelist
2. `MaxOrdersPerMinute` — Denies if orders in last 60s ≥ limit
3. `OneOrderPerSymbolInFlight` — Denies if a prior order/position is open for that symbol
4. `RequireStopLoss` — Denies BUY intents without a `stop_price`
5. `MaxDailyLoss` — Denies if daily PnL ≤ -max_loss_pct × equity
6. `KillSwitchFile` — Denies if the kill-switch file exists
7. `MaxOpenPositions` — Denies BUY if open positions ≥ cap
8. `CooldownAfterLoss` — Denies BUY if consecutive_losses ≥ threshold
9. `MaxPositionSizePct` — Denies BUY if notional / equity > cap
10. `MaxGrossExposurePct` — Denies BUY if (gross_exposure + notional) / equity > cap

### 4. Analytics & reporting

| File | Role |
|---|---|
| `src/cryptobot/analytics/queries.py` | `get_equity_curve`, `reconstruct_trades` (FIFO), `daily_summary`, `symbol_breakdown`, `fee_impact` |
| `src/cryptobot/analytics/report.py` | `build_report()` — full text report + pre-live checklist |

**Pre-live checklist gates:** Sharpe > 1.0, max DD < 20%, ≥ 30 trades, win rate > 40%, fees < 15% of gross.

**Known limitation:** `n_bars_held` is hardcoded to `0` in `reconstruct_trades()` for DB-sourced trades (no bar-level timestamps in the fill table). Not a bug; noted for future improvement.

### 5. Telegram notifications

| File | Role |
|---|---|
| `src/cryptobot/monitoring/notify.py` | `Notifier.send()` via stdlib `urllib`; silently swallows errors; no-op when unconfigured |

**Wired in `run_paper.py` for:** run start/end, every fill (BUY/SELL), stop-loss trigger, daily loss cap denial.

### 6. Journal & persistence

| File | Role |
|---|---|
| `src/cryptobot/journal/models.py` | SQLAlchemy models: Run, SignalRow, OrderRow, FillRow, EquitySnapshotRow |
| `src/cryptobot/journal/writer.py` | `init_db`, `record_run_start/signal/order/fill/equity_snapshot/run_end` |

### 7. Data layer

| File | Role |
|---|---|
| `src/cryptobot/data/storage.py` | `BarStore` — parquet-backed, atomic writes, dedup on `ts_open` |
| `src/cryptobot/data/loader.py` | `HistoricalLoader` — paginated CCXT fetch, gap-skip-and-continue, idempotent |

### 8. CLI commands

| Command | Status |
|---|---|
| `cryptobot version` | Working |
| `cryptobot init-db` | Working |
| `cryptobot backtest --config <yaml> --data <csv>` | Working |
| `cryptobot paper --config <yaml>` | Working |
| `cryptobot fetch-history --symbol --timeframe --since [--until]` | Working |
| `cryptobot report [--run-id] [--list]` | Working |
| `cryptobot live` | Intentional stub — raises `LiveTradingDisabled` |

### 9. Strategy layer

| File | Role |
|---|---|
| `src/cryptobot/strategy/base.py` | `Strategy` ABC + `StrategyContext` dataclass |
| `src/cryptobot/strategy/registry.py` | `@register_strategy`, `get_strategy`, `registered_names` |
| `src/cryptobot/strategy/sma_crossover.py` | SMA(20,50) crossover with ATR-based sizing and stop-loss |

**Currently registered:** `['sma_crossover']`

---

## Stubs, gaps, and deferred items

| Item | Status | Phase |
|---|---|---|
| `app/run_live.py` | Intentional stub — raises `LiveTradingDisabled` | Phase 6 |
| `LiveBroker` | Does not exist | Phase 6 |
| Partial fill handling | Not implemented — paper assumes all-or-nothing | Phase 6 |
| Exchange minimum order size / precision enforcement | Not implemented | Phase 6 |
| Order reconciliation on restart | Not implemented | Phase 6 |
| Walk-forward backtest utility | Not implemented | Post-Phase 7 |
| Ensemble / RSI / Donchian / Bollinger / VolumeSignal strategies | Not implemented | Phase 7 |

No `NotImplementedError` or `TODO` blockers in any completed-phase code paths.

---

## plan.md vs README.md accuracy

Both are accurate on phase status. One README inaccuracy found:

- **README roadmap table** labels Phase 7 as "Analytics + iteration" — that work was completed in
  Phase 5. Phase 7 is now the ensemble strategy system. Fix: update the Phase 7 row in the README
  roadmap table.

---

## Verification commands

```bash
# Phase 1 — scaffold, config, logging, risk rules
pytest tests/test_risk_rules.py tests/test_settings.py -v

# Phase 2 — bar storage + historical loader
pytest tests/test_bar_store.py tests/test_historical_loader.py -v

# Phase 3 — strategy + backtest engine
pytest tests/test_sma_crossover.py tests/test_backtest_engine.py -v
cryptobot backtest --config config/backtest.yaml --data <your_ohlcv.csv>

# Phase 4 — paper trading (needs exchange keys in .env)
cryptobot paper --config config/paper.yaml

# Phase 5 — analytics report (needs a completed run in the journal)
cryptobot init-db
cryptobot report --list
cryptobot report

# All phases at once
pytest   # should be 111+ passing, 0 failing

# Verify strategy registry (Phase 7 not yet implemented)
python -c "from cryptobot.strategy.registry import registered_names; print(registered_names())"
# Now:          ['sma_crossover']
# After Phase 7: ['bollinger', 'donchian', 'ensemble', 'rsi', 'sma_crossover', 'volume_signal']
```
