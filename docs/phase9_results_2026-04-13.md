# Phase 9 — Strategy Validation Results
**Date:** 2026-04-13  
**Branch:** `claude/review-trading-bot-5XHVM`  
**Data:** BTC/USDT 1h, 2023-01-01 → 2026-04-13, 28,275 bars (Binance US)

---

## Bugs Fixed This Session

### 1. `OneOrderPerSymbolInFlight` blocked all SELL exits (critical)
**File:** `src/cryptobot/risk/rules.py`  
The rule was missing the standard `Side.SELL → allow` guard that every other rule has. While in a position, `open_intents_by_symbol` has the symbol set, which caused every bearish-crossover SELL to be denied. All trades were forced to end on stop-loss, producing an artificial **0% win rate** across all walk-forward folds. Fixed by adding the SELL exemption.

### 2. Position sizing 10–20× over the risk cap (critical)
**Files:** `config/backtest.yaml`, `config/default.yaml`, `src/cryptobot/strategy/ensemble.py`  
ATR-based sizing produces positions of 20–95% of equity (because ATR is a small % of BTC's price). The `MaxPositionSizePct` rule at 10% was blocking every entry, yielding 0 trades. Fixed by adding `max_position_notional_pct: 0.09` to strategy params so the strategy self-clips before hitting the risk rule.

### 3. ADX filter added to `SmaCrossover`
**Files:** `src/cryptobot/strategy/sma_crossover.py`, `src/cryptobot/strategy/base.py`  
New params `adx_window` (default 14) and `adx_threshold` (default 0 = disabled). When `adx_threshold > 0`, entries are gated on ADX ≥ threshold to filter choppy markets. Added `_compute_adx()` Wilder-smoothed implementation to `strategy/base.py` as a shared helper.

---

## Walk-Forward Results (5 folds, fixed params: fast=20, slow=50, ADX≥25)

> All rules active: MaxDailyLoss, MaxPositionSizePct, OneOrderPerSymbolInFlight (fixed), etc.

```
====================================================================================
  WALK-FORWARD  BTC/USDT  1h  (5 folds)
====================================================================================
  Fold          Window            Sharpe  Sortino    MaxDD   WinRate  Trades    Return
    1IN   2023-01-01 -> 2023-07-05      1.91    3.19    -0.8%     30.8%      13     +1.6%
    1OUT  2023-07-05 -> 2023-09-14     -3.36   -4.18    -0.7%      0.0%       7     -0.5%
    2IN   2023-09-14 -> 2024-02-26     -1.38   -1.75    -1.1%     10.0%      10     -0.6%
    2OUT  2024-02-26 -> 2024-05-07     -3.70   -4.23    -0.5%      0.0%       2     -0.5%
    3IN   2024-05-07 -> 2024-10-18     -0.45   -0.65    -0.9%     41.2%      17     -0.3%
    3OUT  2024-10-19 -> 2024-12-28     -2.51   -3.08    -0.3%      0.0%       2     -0.3%
    4IN   2024-12-28 -> 2025-06-11     -1.51   -1.98    -2.2%     21.4%      14     -1.3%
    4OUT  2025-06-11 -> 2025-08-21     -4.03   -4.35    -0.2%      0.0%       2     -0.2%
    5IN   2025-08-21 -> 2026-02-02     -1.50   -1.94    -1.1%     25.0%      16     -0.9%
    5OUT  2026-02-02 -> 2026-04-13     -1.72   -2.35    -0.8%     42.9%       7     -0.6%
          OUT-OF-SAMPLE MEAN        -3.07   -3.64    -0.5%      8.6%      20     -0.4%
             Calmar (mean)        -4.52  |  Max consec. losses: 7
====================================================================================
```

**Note:** Very small OOS windows (~70 days / ~1,700 bars per fold). 1–7 trades per OOS fold makes statistics unreliable.

---

## Full Dataset Backtest — SMA Crossover (fast=20, slow=50, ADX≥25)

| Metric | Value |
|---|---|
| Bars | 28,275 (Jan 2023 – Apr 2026) |
| Trades | 94 |
| Win rate | 24.5% |
| Sharpe | -0.60 |
| Sortino | -0.85 |
| Calmar | -0.17 |
| Total return | **-2.79%** |
| Max drawdown | 5.13% |
| Profit factor | 0.71 |
| Max consec. losses | 11 |
| Time in market | 10.6% |

**Equity curve:**
```
2023-01-01  $10,000  (start)
2023-05-19  $10,190  (peak)
2023-09-14  $10,105
2024-01-10  $10,061
2024-05-07  $10,147
2024-09-01  $10,118
2024-12-28  $10,081
2025-04-25  $9,933
2025-08-21  $9,903
2025-12-16  $9,828
2026-04-13  $9,721  (final, -2.79%)
```

---

## Full Dataset Backtest — Ensemble (RSI + Donchian + Bollinger + Volume + Regime)

| Metric | Value |
|---|---|
| Trades | 448 |
| Win rate | 19.4% |
| Sharpe | -0.52 |
| Sortino | -0.73 |
| Total return | **-4.72%** |
| Max drawdown | 9.90% |
| Profit factor | 0.87 |
| Max consec. losses | **41** |
| Time in market | 38.3% |

---

## Strategy Comparison

| Metric | SMA Crossover (1h) | Ensemble (1h) |
|---|---|---|
| Trades | 94 | 448 |
| Win rate | **24.5%** | 19.4% |
| Sharpe | -0.60 | **-0.52** |
| Total return | **-2.79%** | -4.72% |
| Max drawdown | **5.13%** | 9.90% |
| Max consec. losses | **11** | 41 |
| Time in market | **10.6%** | 38.3% |

Both strategies lose on 1h BTC. The ensemble trades 5× more frequently and generates more than twice the drawdown. More signals = more noise on this timeframe.

---

## Diagnosis

**Root cause:** 1h BTC produces too many false crossovers. On 1h bars, both SMA crossover and the ensemble see the same underlying noise. The ADX filter reduces trade count and drawdown (-5% vs -10% for ensemble) but doesn't fix signal quality — it just takes fewer bad trades.

The equity curves show a slow, steady bleed across all market regimes and time periods. This is not a regime problem or a parameter problem. It is a **timeframe problem**.

For reference: BTC buy-and-hold returned roughly +400% over the same period ($16k → $84k). Both strategies massively underperformed buy-and-hold while being out of the market ~62–90% of the time.

---

## Recommended Next Step

**Test SMA crossover on 4h BTC** before any further feature work.

Rationale:
- SMA crossovers are a documented edge on higher timeframes (4h, daily)
- 4h has 6× less noise than 1h — fewer false crossovers per year
- Same strategy, same code, same risk rules — only the timeframe changes
- The existing 28,275 1h bars can be resampled to ~7,000 4h bars immediately (no new data fetch needed)
- If 4h also fails: switch to a genuinely different strategy class (e.g. RSI mean-reversion, breakout with volume confirmation)

If 4h OOS Sharpe > 0: proceed to paper trading validation.  
If 4h OOS Sharpe < 0: the SMA crossover family has no edge on BTC; investigate RSI mean-reversion or breakout strategies.

---

## Pre-Live Checklist Gates (for reference)

| Gate | Threshold | Status |
|---|---|---|
| OOS Sharpe | > 1.0 | ❌ |
| OOS Sortino | > 1.0 | ❌ |
| Max drawdown | < 20% | ✅ |
| Min trades | ≥ 30 | ✅ (full dataset) |
| Win rate | > 40% | ❌ |

**Phase 6 (live trading) remains blocked.** Strategy does not pass pre-live checklist.
