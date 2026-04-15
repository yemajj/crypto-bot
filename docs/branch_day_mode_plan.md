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

## How to Use This File

At the start of each Claude session:
- read this file
- align all work to this plan
- do not drift into unrelated ideas

This file acts as the source of truth for this branch.