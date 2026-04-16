# Branch Plan: Day Mode Research

## Purpose

This branch is dedicated to researching a day-trading strategy that is structurally different from previously failed approaches.

This is a separate track from swing-mode validation. Do not modify or interfere with swing-mode logic or configs in this branch.

## Current Context

Previous day-trading strategies on BTC failed:
- RSI mean reversion
- SMA crossover (intraday)
- Donchian breakout (with and without ADX)
- Opening Range Breakout (ORB)

Failures were structural, not just parameter-related:
- too much noise on lower timeframes
- low win rates
- negative expectancy after fees
- overtrading or false signals

Key insight:
Single-symbol BTC indicator-trigger strategies are not producing reliable edge for day trading.

## New Direction

Focus on multi-symbol day-mode strategies that generate opportunities at the portfolio level, not by forcing trades from BTC alone.

### Primary Strategy Class

Start with:

Cross-sectional relative strength / momentum

High-level idea:
- evaluate a small basket of symbols
- rank them by short-term strength / momentum
- trade the strongest candidates when conditions are favorable
- allow the portfolio to generate trades instead of relying on one symbol

This is intentionally different from:
- RSI threshold triggers
- Donchian breakouts
- ORB-style session breakouts
- simple SMA crossovers

## Scope

### In scope
- implementing one new day-mode strategy class
- testing on a small basket of symbols (e.g., BTC, SOL, LINK, etc.)
- portfolio-level trade generation
- simple, clean logic
- realistic backtesting (fees, slippage assumptions)
- verdict-first reporting

### Out of scope
- modifying swing-mode configs or logic
- parameter optimization / curve fitting
- adding multiple strategies at once
- redesigning the entire bot architecture
- copying external frameworks wholesale

## Implementation Approach

1. Propose 2–3 candidate designs within the relative-strength / momentum class
2. Select ONE to implement first
3. Build a minimal version:
   - strategy file
   - config
   - basic tests (correctness, not performance tuning)
4. Run initial backtest on a small basket
5. Evaluate using portfolio-level metrics

## Metrics to Report

- total return
- Sharpe ratio
- max drawdown
- total trades
- trades per day (portfolio level)
- win rate
- expectancy
- fee impact
- per-symbol contribution

## Verdict Criteria

At the end of each test, give a blunt verdict:

- promising → continue and refine
- mixed → one more validation step needed
- weak → abandon this direction and try a different strategy class

## Guardrails

- do not reuse failed strategy logic with minor tweaks
- do not tune parameters prematurely
- do not add complexity without clear reason
- keep implementations simple and testable
- prioritize structural differences over small improvements

## Success Criteria for This Branch

This branch is successful if it produces:

- a day-mode strategy direction with credible potential, OR
- a clear conclusion that this strategy class does not work

## Merge Criteria

Merge into main only if:
- a strategy shows consistent promise across validation
- code is clean and documented
- results are reproducible
- direction is clearly defined

Otherwise, keep as a research branch or discard.

## Research Log

### XSMOM — Cross-Sectional Rate-of-Change Momentum (COMPLETED, WEAK)

Implementation: `src/cryptobot/strategy/xsmom.py`, `src/cryptobot/app/run_xsmom_backtest.py`

Architecture: one shared strategy instance maintains `_roc_cache` (symbol → N-bar RoC) across all symbols. Symbols ranked by RoC percentile each bar. Buy top tier (rank ≥ 0.66) with positive absolute momentum. Exit on `roc < 0` (primary) or bottom-third rank (secondary). ATR-based stop sizing.

| Run | Timeframe | Lookback | Trades/day | Win rate | Sharpe | Avg return | Fees |
|-----|-----------|----------|------------|----------|--------|------------|------|
| 15m | 15m | 20 bars (5h) | 13.3 | 16.5% | -15.07 | -68.1% | $15,325 |
| 1h  | 1h  | 20 bars (20h) | 2.5  | 29.3% | -2.30  | -12.84% | $3,114 |

**Verdict: WEAK on both timeframes.**

The 15m result failed primarily due to fee drag (5h window → 13 trades/day → $15k fees on $30k capital). The 1h result isolates the signal: even with modest fee drag ($3.1k on $30k), gross P&L before fees is near-zero or negative. Win rate of 29% is well below breakeven. The ranking signal itself has no positive expectancy on this basket/period.

**Conclusion: abandon XSMOM. Do not tune parameters. Signal family does not work.**

### VRFMR — Volatility-Regime Filtered Mean Reversion (COMPLETED, WEAK)

Implementation: `src/cryptobot/strategy/vrfmr.py`, config `config/backtest_vrfmr.yaml`

Architecture: ADX < 25 gates entries to range-bound conditions only. RSI < 30 triggers BUY with ATR-based stop. Exit at RSI ≥ 50 (mean reversion complete) or ADX ≥ 25 (regime flip). Per-symbol; no shared state. Runner: existing `cryptobot multi-backtest`.

| Symbol | Return | Sharpe | Win Rate | Trades | Gross P&L (before fees) |
|--------|--------|--------|----------|--------|------------------------|
| BTC/USDT | -10.3% | -2.59 | 41% | 346 | -$509 |
| SOL/USDT | +0.4% | 0.07 | 45% | 351 | **+$605** |
| LINK/USDT | -9.9% | -2.01 | 39% | 345 | -$469 |
| **Portfolio** | **-6.6%** | **-2.11** | **41.6%** | **1,042** | **Total fees: $1,615** |

**Verdict: WEAK at portfolio level.**

However, this is the most informative negative result so far:
- **Win rate 41.6%** — highest of any strategy tested; within striking distance of breakeven
- **Fees are not the problem** — $1,615 on $30k capital (5.4% drag), far below XSMOM's 51%
- **SOL has a positive gross signal**: +$605 before fees → the ADX regime gate is working for SOL
- **BTC and LINK are dragging**: gross P&L negative on both; the regime filter does not isolate clean range-bound periods for these symbols on this dataset
- **One symbol dominates**: SOL contributes 100% of positive PnL — diversification benefit is absent

**Conclusion: VRFMR has a partial signal (SOL) but does not generalize across the basket. Do not tune parameters. The structural question is whether BTC/LINK are simply less range-bound in this dataset or whether the regime detection is miscalibrated for those volatility profiles.**

### VSBR — Volume-Surge Breakout (COMPLETED, WEAK)

Implementation: `src/cryptobot/strategy/vsbr.py`, config `config/backtest_vsbr.yaml`

Architecture: per-symbol, no shared state. Entry requires two simultaneous conditions: (1) current volume >= 2× 20-bar rolling average volume, (2) current close >= 20-bar rolling high. Exit on close < 10-bar trailing EMA. ATR-based stop sizing.

| Symbol | Return | Sharpe | Win Rate | Trades | Fees |
|--------|--------|--------|----------|--------|------|
| BTC/USDT | -30.3% | -6.29 | 18% | 2,052 | $2,786 |
| SOL/USDT | -30.0% | -3.36 | 22% | 2,116 | $2,983 |
| LINK/USDT | -34.5% | -4.28 | 22% | 2,166 | $2,881 |
| **Portfolio** | **-31.6%** | **-6.12** | **21%** | **6,334** | **$8,650** |

Timeframe: 15m | Trades/day: 5.3 | Period: 2023-01-01 → 2026-04-15

**Verdict: WEAK.**

The volume surge condition is not selective enough at 15m. The strategy generated 5.3 trades/day (similar to XSMOM 1h), with a 21% win rate — catastrophically below breakeven. The core problem: on 15m bars, volume spikes near local highs are common noise events, not genuine institutional breakouts. The two conditions fire together frequently rather than rarely.

Gross P&L is deeply negative independent of fees. The signal has no edge.

**Conclusion: abandon VSBR at 15m. The volume-surge + price-breakout combination does not filter false breakouts at short timeframes. Do not tune parameters.**

**Engineering note:** session also fixed two latent performance bugs:
- `risk_allowed` was logged at `INFO` level (now `DEBUG`) — was flooding log on high-frequency strategies
- `run_multi_backtest.py` was writing 100k+ equity snapshots per symbol to SQLite (now skipped — results go to files only)

### Next Candidates

One candidate remains for day-mode research:

- **Time-of-day filtered ORB variant**: narrow the ORB entry window, add a volume confirmation gate, and filter to specific session hours known to have directional bias. Structurally different from the prior ORB failure (broader window, no volume filter).

---

## How to Use This File

At the start of each Claude session:
- read this file
- align all work to this plan
- do not drift into unrelated ideas

This file acts as the source of truth for this branch.