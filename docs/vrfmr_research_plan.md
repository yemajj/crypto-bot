# VRFMR Research Plan: Volatility-Regime Filtered Mean Reversion

## Background

Branch `claude/day-mode-strategy-research-60mko` is dedicated to finding a structurally different day-mode strategy. Five approaches have been tested and rejected:

| Strategy | Result | Root Cause |
|---|---|---|
| RSI mean reversion | WEAK | No regime filter; trades in trends |
| SMA crossover (intraday) | WEAK | Noise dominates on lower timeframes |
| Donchian breakout (w/ and w/o ADX) | WEAK | False breakouts, negative expectancy |
| Opening Range Breakout (ORB) | WEAK | No volume gate, broad entry window |
| XSMOM cross-sectional momentum | WEAK | Signal has no positive expectancy at 1h or 15m |

Next candidate: **volatility-regime filter + mean reversion**.

Key insight from RSI failure: RSI < 30 in a downtrend is not a mean reversion signal — it's a continuation signal. The fix is a regime gate that blocks all entries during trending conditions.

---

## Strategy Design

### Name: `vrfmr` (Volatility-Regime Filtered Mean Reversion)

### Regime Filter: ADX < threshold

**Indicator**: Average Directional Index (ADX)
- Already implemented in codebase: `_compute_adx(bars, window)` in `src/cryptobot/strategy/base.py`
- Range: [0, 100]. Values ≥ 25 indicate a meaningful directional trend.
- Gate: `adx < adx_threshold` (default 25.0) → range-bound, OK to trade

**Why ADX over alternatives**:
- ATR ratio (short / long ATR): less interpretable, two windows to tune
- Bollinger Band width: measures volatility compression, not directionality
- ADX directly answers "is this market trending?" — canonical indicator for exactly this purpose

### Entry Signal: RSI < oversold

**Indicator**: RSI (Wilder's method)
- Already implemented: `_rsi(closes, window)` in `src/cryptobot/strategy/rsi.py`

**Entry conditions (both required)**:
1. `adx < adx_threshold` — range-bound regime
2. `rsi < rsi_oversold` (default 30.0) — price oversold relative to recent history

**ATR-based position sizing + stop**:
- `stop_distance = atr * stop_distance_multiplier`
- `qty = min(equity * risk_per_trade_pct / stop_distance, equity * max_position_notional_pct / price)`

### Exit Logic: Two Conditions (First Hit Wins)

1. **Mean reversion complete**: `rsi >= rsi_exit` (default 50.0)
   - Exit at midpoint, not overbought — captures the bounce back to mean without overstaying
2. **Regime flip**: `adx >= adx_threshold` — trend starting, exit defensively

**Why exit at RSI 50, not 70**:
- Targets the return-to-mean, not the full overbought extension
- Shorter hold → less exposure to regime transitions mid-trade
- The prior RSI strategy exited at 70 and gave back gains during trending moves

### Structural Difference from Prior RSI Failure

| Dimension | Prior RSI (`rsi.py`) | VRFMR |
|---|---|---|
| Entry gate | RSI < 30, any market | RSI < 30 AND ADX < 25 |
| Exit trigger | RSI > 70 | RSI ≥ 50 OR regime flip |
| Trending market | Trades (and loses) | Blocked at entry, exits if regime flips while in position |
| Architecture | `ScoringStrategy` subclass | `Strategy` subclass, explicit regime-aware logic |

The regime gate is the structural difference. The RSI signal itself is the same; what's new is the market condition filter that determines when that signal is valid.

---

## Parameters (First-Pass, No Tuning)

| Parameter | Default | Rationale |
|---|---|---|
| `adx_window` | 14 | Wilder's standard window |
| `adx_threshold` | 25.0 | Classic non-trending boundary |
| `rsi_window` | 14 | Standard RSI window |
| `rsi_oversold` | 30.0 | Classic oversold threshold |
| `rsi_exit` | 50.0 | Mean (midpoint) — conservative exit |
| `atr_window` | 14 | Consistent with other strategies |
| `stop_distance_multiplier` | 1.5 | Matching XSMOM |
| `risk_per_trade_pct` | 0.005 | 0.5% equity risk per trade |
| `max_position_notional_pct` | 0.08 | Cap at 8% of equity per position |

All first-pass values. No optimization.

---

## Implementation Plan

### Files to Create

#### 1. `src/cryptobot/strategy/vrfmr.py` (~90 lines)

```python
@register_strategy("vrfmr")
class VolatilityRegimeMeanReversionStrategy(Strategy):
    name = "vrfmr"

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        # Read params
        # Guard: need 2*adx_window+1 bars minimum
        # Compute ADX, determine regime
        # Compute RSI
        # EXIT: has_position AND (rsi >= rsi_exit OR not in_regime) → SELL
        # ENTRY: not has_position AND in_regime AND rsi < rsi_oversold → BUY with stop
```

Imports: `_compute_adx`, `_compute_atr` from `base.py`; `_rsi` from `rsi.py` (same package, reuse existing implementation).

#### 2. `src/cryptobot/strategy/__init__.py` (1 line change)

Add `from cryptobot.strategy import vrfmr  # noqa: F401` after the `xsmom` import. Required to trigger `@register_strategy` at startup.

#### 3. `config/backtest_vrfmr.yaml`

```yaml
mode: backtest
market:
  symbols: [BTC/USDT, SOL/USDT, LINK/USDT]
  timeframe: 1h
strategy:
  name: vrfmr
  params:
    adx_window: 14
    adx_threshold: 25.0
    rsi_window: 14
    rsi_oversold: 30.0
    rsi_exit: 50.0
    atr_window: 14
    stop_distance_multiplier: 1.5
    risk_per_trade_pct: 0.005
    max_position_notional_pct: 0.08
risk:
  max_open_positions: 3        # all symbols independent, no cross-sectional cap
  cooldown_after_losses: 0
  max_position_pct: 0.10
  max_gross_exposure_pct: 0.30
  max_daily_loss_pct: 0.03
  max_orders_per_minute: 10
  require_stop_loss: true
  symbol_allow_list: [BTC/USDT, SOL/USDT, LINK/USDT]
fees:
  taker_bps: 10.0
  maker_bps: 5.0
  slippage_bps: 5.0
starting_cash: 10000.0
warmup_bars: 30               # covers ADX warmup: 2*14+1 = 29 bars
```

#### 4. `tests/test_vrfmr.py` (~120 lines)

| Test | Validates |
|---|---|
| `test_insufficient_bars_returns_empty` | warmup guard |
| `test_trending_regime_blocks_entry` | high ADX + oversold RSI → no BUY |
| `test_range_bound_oversold_generates_buy` | low ADX + low RSI → BUY |
| `test_buy_intent_has_stop_price` | `stop_price is not None` |
| `test_range_bound_not_oversold_no_entry` | low ADX, neutral RSI → [] |
| `test_exit_on_rsi_recovery` | has_position, RSI ≥ 50 → SELL |
| `test_exit_on_regime_flip` | has_position, ADX ≥ threshold → SELL |
| `test_hold_in_regime_open_rsi` | has_position, low ADX, RSI < exit → [] |
| `test_sell_intent_has_no_stop_price` | SELL `stop_price is None` |
| `test_registered_as_vrfmr` | registry lookup |

### No New Runner Needed

VRFMR has no shared cross-sectional state (unlike XSMOM which needed `_roc_cache` shared across symbols). Each symbol's strategy instance is independent. The existing `cryptobot multi-backtest` CLI handles this correctly:

```bash
cryptobot multi-backtest \
    --config config/backtest_vrfmr.yaml \
    --data-dir data/ \
    --out-dir results/vrfmr/
```

---

## Data

Real 1h OHLCV files already available:
- `data/BTC_USDT_1h.csv` — 20,040 bars
- `data/SOL_USDT_1h.csv` — 20,040 bars
- `data/LINK_USDT_1h.csv` — 20,040 bars

Timeframe: 1h chosen over 15m based on XSMOM lesson (15m → too many trades, fee drag dominates).

---

## Metrics to Report

- Total return (avg equal-weight)
- Portfolio Sharpe
- Portfolio max drawdown
- Total trades + trades/day (portfolio level)
- Win rate (all symbols)
- Expectancy (PnL per trade)
- Total fees paid
- Per-symbol contribution table
- Pairwise correlation matrix

**Verdict thresholds**:
- Sharpe ≥ 0.5, return > 0, win rate ≥ 45% → **promising**
- Sharpe ≥ 0.1 or return > 0 with win rate ≥ 40% → **mixed**
- Otherwise → **weak**

---

## Open Questions for Review

1. **ADX threshold**: 25 is the canonical boundary. Should it be lower (20) to be more conservative about regime detection, or stay at 25?
2. **RSI exit at 50 vs 55**: Exiting at 50 is conservative. Some mean reversion strategies exit at 55–60 to give the bounce more room. Is 50 too tight?
3. **`max_open_positions: 3`**: Allows all three symbols to hold simultaneously. With $10k each, max total exposure is ~24% (3 × 8% notional cap per position). Alternatively, cap at 2 to reduce simultaneous exposure. Recommend 3 for the first pass — let the regime filter do the work.
4. **Single-symbol vs basket**: Could this be tested on BTC only first to isolate the signal before going multi-symbol? Benefit: cleaner initial read. Downside: delays seeing diversification effects. Current plan: go multi-symbol directly (matching the research branch scope).
