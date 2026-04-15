# Day-Mode Strategy Research Plan: Cross-Sectional Momentum (XSMOM)

## Context

Branch `claude/day-mode-strategy-research-60mko` is dedicated to finding a structurally different day-mode strategy after four failures (RSI, SMA crossover, Donchian, ORB) — all single-symbol indicator-trigger strategies that produced negative expectancy on BTC 1h.

The new direction per `docs/branch_day_mode_plan.md`: multi-symbol, portfolio-level signal generation via **cross-sectional relative strength / momentum** — rank a basket of symbols by short-term return, trade the strongest, skip the rest.

---

## Candidate Designs

### C1 — Cross-Sectional Rate-of-Change Momentum (XSMOM) ✅ SELECTED
- Each bar: compute the N-bar return for the symbol; update a shared cross-sectional cache
- Rank this symbol against all others in the cache (percentile 0→1)
- BUY if rank ≥ threshold AND absolute RoC > 0 (positive momentum, top tier)
- EXIT if rank < 0.5 OR RoC < 0 (relative or absolute weakness)
- ATR-based stop on entry

### C2 — Cross-Sectional Z-Score Mean Reversion
- Compute each symbol's RoC; normalize to a z-score within the cross-section
- Buy the most oversold relative to peers; exit when z-score reverts
- Contrarian, more moving parts, harder to test cleanly

### C3 — Volatility-Adjusted Momentum (Momentum Sharpe)
- Rank by N-bar return / N-bar realized vol → quality-adjusted momentum
- Natural extension of C1 if C1 shows promise; not the first step

### Selection Rationale (C1)
- Simplest correct implementation of the cross-sectional idea
- No look-ahead: each symbol uses only its own history; shared cache accumulates naturally as the runner calls the strategy sequentially per symbol
- Academically validated class (Jegadeesh-Titman momentum, widely replicated in crypto)
- Clearly distinct from all four failed strategies — signal is inter-symbol rank, not a single-symbol indicator threshold
- Clean unit-testable: two symbols, known RoC values → predictable rank → predictable intent

---

## Architecture Decision

The existing `run_multi_backtest.py` creates **one strategy instance per symbol** — meaning `_roc_cache` state would be isolated per symbol, breaking cross-sectional ranking.

Fix: create a new minimal runner (`run_xsmom_backtest.py`) that:
1. Creates **one shared strategy instance** across all symbols
2. Groups all symbol bars by timestamp
3. Processes each timestamp in order, calling `strategy.on_bar()` for each symbol present
4. The strategy's `_roc_cache` accumulates across all symbols → real cross-sectional ranking
5. Per-symbol `BacktestBroker` tracks positions/fills independently
6. Reuses existing `compute_portfolio_metrics` + `write_portfolio_summary` for output

This is ~150 lines of new runner code; all other infrastructure is reused unchanged.

---

## Files to Create

### 1. `src/cryptobot/strategy/xsmom.py` (~90 lines)

```python
@register_strategy("xsmom")
class CrossSectionalMomentumStrategy(Strategy):
    """Cross-sectional RoC momentum.

    Stateful: maintains _roc_cache (symbol → N-bar return) across calls.
    Call order within each bar must sweep all symbols before the next bar.
    """
    name = "xsmom"

    def __init__(self, params=None):
        super().__init__(params)
        self._roc_cache: dict[str, float] = {}

    def on_bar(self, ctx) -> list[Intent]:
        lookback = params.get("lookback", 20)
        top_pct   = params.get("top_pct",   0.34)
        min_syms  = params.get("min_symbols", 2)

        if len(ctx.history) < lookback + 1:
            return []

        roc = (close[-1] - close[-lookback]) / close[-lookback]
        self._roc_cache[ctx.symbol] = roc

        if len(self._roc_cache) < min_syms:
            return []

        rank = sum(r <= roc for r in self._roc_cache.values()) / len(self._roc_cache)
        threshold = 1.0 - top_pct
        has_pos = ctx.position.qty > 0

        if rank >= threshold and roc > 0 and not has_pos:
            # BUY: ATR-based size + stop
            ...
        elif has_pos and (rank < 0.5 or roc < 0):
            # EXIT: rank weakness or negative momentum
            ...
        return []
```

Key invariants:
- Only reads from `ctx.history` (pure per-symbol data) — no I/O
- `_roc_cache` is internal state only, never touches broker/journal
- Requires `stop_price` on BUY intents (satisfies `RequireStopLoss` rule)
- SELL intents have no `stop_price` (rule exempts SELL, confirmed by phase-9 bugfix)

### 2. `src/cryptobot/app/run_xsmom_backtest.py` (~170 lines)

Minimal runner:
- `load_all_bars(symbols, timeframe, data_dir)` — reuses `load_bars_from_csv`
- `run_portfolio(settings, all_bars)` — shared strategy instance; per-symbol broker + risk; timestamp-interleaved loop
- `main(config_path, data_dir, out_dir)` — CLI entry point; calls `compute_portfolio_metrics` + `write_portfolio_summary`

Risk state built per-symbol per-bar (same pattern as `_run_symbol` in `run_backtest.py`).
Rules applied: `SymbolAllowList`, `MaxOrdersPerMinute`, `OneOrderPerSymbolInFlight`, `RequireStopLoss`, `MaxDailyLoss`, `MaxPositionSizePct`, `MaxGrossExposurePct`, `KillSwitchFile`.

### 3. `config/backtest_xsmom.yaml`

```yaml
mode: backtest
market:
  symbols: [BTC/USDT, SOL/USDT, LINK/USDT]
  timeframe: 1h
strategy:
  name: xsmom
  params:
    lookback: 20            # ~20h momentum window
    top_pct: 0.34           # buy top ~1/3 of basket
    atr_window: 14
    risk_per_trade_pct: 0.005
    stop_distance_multiplier: 1.5
    max_position_notional_pct: 0.08
    min_symbols: 2
risk:
  max_position_pct: 0.10
  max_gross_exposure_pct: 0.30
  max_daily_loss_pct: 0.03
  max_orders_per_minute: 10
  require_stop_loss: true
  symbol_allow_list: [BTC/USDT, SOL/USDT, LINK/USDT]
  max_open_positions: 2
  cooldown_after_losses: 3
  cooldown_bars: 6
fees:
  taker_bps: 10.0
  maker_bps: 5.0
  slippage_bps: 5.0
starting_cash: 10000.0
warmup_bars: 24
```

### 4. `tests/test_xsmom.py` (~130 lines)

Tests (mirror `test_donchian.py` / `test_orb.py` patterns):

| Test | Validates |
|---|---|
| `test_insufficient_bars_returns_empty` | `len(history) < lookback+1 → []` |
| `test_single_symbol_below_min_returns_empty` | `len(_roc_cache) < min_symbols → []` |
| `test_top_ranked_positive_roc_generates_buy` | rank=1.0, roc>0 → BUY with stop_price |
| `test_bottom_ranked_no_buy` | rank=0.0 → [] even if roc>0 |
| `test_top_ranked_negative_roc_no_buy` | rank=1.0 but roc<0 → [] |
| `test_exit_on_low_rank` | has_position, rank drops < 0.5 → SELL |
| `test_exit_on_negative_roc` | has_position, roc turns negative → SELL |
| `test_hold_when_rank_high` | has_position, rank still high → [] |
| `test_buy_intent_has_stop_price` | stop_price not None on BUY |
| `test_registered_as_xsmom` | `get_strategy("xsmom") is CrossSectionalMomentumStrategy` |

---

## Backtest Execution Steps

1. **Check data** — look for `data/BTC_USDT_1h.csv`, `data/SOL_USDT_1h.csv`, `data/LINK_USDT_1h.csv`
2. **Fetch if missing** — `cryptobot fetch-history --symbol BTC/USDT --timeframe 1h --since 2023-01-01` then `cryptobot export-history ...` for each symbol
3. **Run backtest** — `python -m cryptobot.app.run_xsmom_backtest config/backtest_xsmom.yaml --data-dir data/ --out-dir results/xsmom/`
4. **Report** — `results/xsmom/portfolio_summary.md` contains the verdict-first report with all required metrics

---

## Verdict Metrics to Report

- Total return (avg equal-weight)
- Portfolio Sharpe
- Portfolio max drawdown
- Total trades + trades/day (portfolio level)
- Win rate (all symbols)
- Expectancy (PnL per trade)
- Total fees paid
- Per-symbol contribution table
- Pairwise correlation matrix

**Verdict thresholds** (from existing `_portfolio_verdict` in `run_multi_backtest.py`):
- Sharpe ≥ 0.5, return > 0, win rate ≥ 45%, no dominant symbol → **promising**
- Sharpe ≥ 0.1 or return > 0 with win rate ≥ 40% → **mixed**
- Otherwise → **weak**

---

## Guardrails Compliance

- No swing-mode files touched
- No reuse of RSI, SMA, Donchian, or ORB logic
- No parameter tuning (first-pass defaults only)
- No new infrastructure beyond what the cross-sectional architecture requires
- `_roc_cache` is internal strategy state only — does not violate the "pure" invariant (no I/O, no broker mutation)

---

## Verification Checklist

```bash
# Unit tests
pytest tests/test_xsmom.py -v

# Full suite must stay green
pytest

# Type check
mypy src/cryptobot/strategy/xsmom.py src/cryptobot/app/run_xsmom_backtest.py

# Lint
ruff check src/cryptobot/strategy/xsmom.py src/cryptobot/app/run_xsmom_backtest.py

# Backtest (after data is available)
python -m cryptobot.app.run_xsmom_backtest config/backtest_xsmom.yaml \
    --data-dir data/ --out-dir results/xsmom/
cat results/xsmom/portfolio_summary.md
```

Commit after each logical unit: strategy file, runner, config+tests, backtest results.

---

## Critical Files Reference

| File | Role |
|---|---|
| `src/cryptobot/strategy/base.py:54` | `_compute_atr()` — reuse for stop sizing |
| `src/cryptobot/strategy/base.py:34` | `Strategy` ABC — subclass directly (not ScoringStrategy) |
| `src/cryptobot/strategy/registry.py` | `@register_strategy("xsmom")` decorator |
| `src/cryptobot/core/types.py` | `Bar`, `Intent`, `Side`, `OrderType`, `Position` |
| `src/cryptobot/backtest/engine.py:98` | Bar-loop pattern to replicate in runner |
| `src/cryptobot/app/run_backtest.py:304` | `_run_symbol()` — pattern for per-symbol risk/broker setup |
| `src/cryptobot/app/run_multi_backtest.py:120` | `compute_portfolio_metrics()` — reuse as-is |
| `src/cryptobot/app/run_multi_backtest.py:225` | `write_portfolio_summary()` — reuse as-is |
| `config/backtest_btc_sol_link.yaml` | Reference multi-symbol config structure |
| `tests/test_donchian.py` | Reference test pattern to follow |
