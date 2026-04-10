# Trading Bot Architecture Review: Current Bot vs. Video Approach

> **Implementation status (2026-04-10):** All P0–P2 items below are **complete**. P3 is
> conditional — only pursue if walk-forward OOS results show the strategy consistently loses.
> Next session: run `cryptobot walk-forward --optimise` on real OHLCV data and interpret results.



## Context

The user wants a blunt, engineering-focused comparison of their existing crypto trading bot against an architecture demonstrated in a YouTube video (HMM regime detection, allocation layer, circuit breakers, broker integration, Streamlit dashboard). The goal is to determine whether to keep building, partially refactor, or start over.

---

## Verdict: KEEP BUILDING

The current bot is production-grade infrastructure that would take months to rebuild. The video approach introduces two genuinely useful architectural ideas (allocation layer, walk-forward optimization) that can be grafted onto the existing codebase. Starting over would be engineering malpractice.

---

## Side-by-Side Comparison

| Dimension | Current Bot | Video Approach | Winner |
|-----------|-------------|----------------|--------|
| **Domain types** | Immutable frozen dataclasses, `Decimal` for all prices/quantities, clean `Intent->Order->Fill` pipeline | Likely simpler, possibly float-based | **Current bot** |
| **Strategy abstraction** | Pure `on_bar()` with no side effects, `ScoringStrategy` ABC for ensemble composition, strategy registry with decorator | Strategy classes, likely coupled to regime output | **Current bot** |
| **Regime detection** | SMA slope + ATR spike heuristic (3 regimes: TRENDING, RANGING, BREAKOUT_WATCH) | HMM-based (probabilistic, data-driven, N regimes) | **Video** (but marginal; see analysis below) |
| **Ensemble/allocation** | 4-bucket weighted voting with regime-aware weight redistribution, agreement filter, volume multiplier | Separate allocation layer that adjusts exposure % by regime | **Tie** — different approaches, both valid |
| **Risk management** | 10 composable rules (symbol allowlist, rate limit, stop-loss enforcement, daily loss cap, position size %, gross exposure %, kill switch, cooldown, max positions) | "Circuit breaker layer" — likely fewer rules | **Current bot** (significantly) |
| **Backtest engine** | Bar-by-bar, correct ordering (settle->stops->equity->strategy->risk->submit), no look-ahead bias verified, fee/slippage modeling | Walk-forward with parameter optimization | **Video** for walk-forward optimization; **current bot** for engine correctness |
| **Paper trading** | Multi-symbol, warmup bars, Telegram notifications, SIGTERM handling, kill switch polling, per-bar equity snapshots, journaling | Paper trading present | **Current bot** |
| **Broker design** | ABC with `BacktestBroker`, `PaperBroker`, live stub; clean interface | Broker integration present | **Tie** |
| **Dashboard** | Streamlit with 6 pages (home, paper control, backtest, config editor, logs, history) | Streamlit dashboard | **Tie** (both Streamlit) |
| **Testing** | 230 tests across 30 files; synthetic helpers, no real exchange calls, deterministic | Unknown (YouTube demos rarely show tests) | **Current bot** (almost certainly) |
| **Config system** | Pydantic-validated YAML + env vars, cross-field validation, secrets separated | Unknown | **Current bot** |
| **Safety controls** | Live trading intentionally disabled, pre-live validation checklist with quantitative gates (Sharpe > 1.0, DD < 20%, >= 30 trades), kill switch | Unknown | **Current bot** |
| **Analytics** | FIFO trade reconstruction, equity curves, daily PnL, symbol breakdown, fee impact analysis, pre-live checklist | Unknown | **Current bot** |
| **Code maturity** | 7 completed phases, clean git history, comprehensive docs, implementation audit | Single demo build | **Current bot** |

**Score: Current Bot 9, Video 1, Tie 3**

---

## Borrow / Avoid / Keep

### KEEP from Current Bot (do not touch)

1. **Intent -> Order -> Fill pipeline** (`core/types.py`) — Immutable, `Decimal`-based domain types. This is textbook DDD. Do not regress.
2. **Pure strategy contract** (`strategy/base.py`) — `on_bar(ctx) -> list[Intent]` with no I/O. This is the correct abstraction.
3. **Risk rule composition** (`risk/rules.py`, `risk/manager.py`) — 10 independently testable rules with short-circuit evaluation. More comprehensive than any YouTube demo.
4. **Backtest bar ordering** (`backtest/engine.py`) — Settle->stops->equity->strategy->risk->submit. Load-bearing. Do not reorder.
5. **Paper trading infrastructure** (`app/run_paper.py`) — Multi-symbol, journaling, warmup, kill switch, SIGTERM.
6. **Journal + analytics** (`journal/`, `analytics/`) — Run-tagged audit trail with FIFO trade reconstruction.
7. **Config system** (`config/settings.py`) — Pydantic validation, env/YAML layering, cross-field rules.
8. **Test suite** — 230 tests. This alone represents weeks of work that would be lost in a rewrite.
9. **Dashboard** (`dashboard/`) — Already functional Streamlit with paper control and backtest visualization.
10. **Ensemble strategy system** (`strategy/ensemble.py`, `signal_aggregator.py`) — Regime-aware weighted voting already exists and works.

### BORROW from Video (targeted additions)

1. **Walk-forward with parameter optimization** — Current walk-forward (`backtest/walk_forward.py`) uses fixed params across all folds. The video approach likely tunes params per in-sample fold, then validates on out-of-sample. This is the single highest-value improvement. Implement as an extension of the existing `run_walk_forward()`, not a replacement.

2. **Allocation layer as a separate concern** — Currently, position sizing lives inside each strategy (`ScoringStrategy.on_bar()` computes qty). Extracting this into a dedicated `Allocator` that sits between Strategy and RiskManager would:
   - Allow regime-based exposure scaling (e.g., half-size in RANGING)
   - Enable Kelly criterion or volatility-targeting without touching strategies
   - Keep strategies focused on *direction* (score), not *size* (qty)
   - This is a clean refactor, not a rewrite.

3. **Richer metrics** — Add Sortino ratio, Calmar ratio, and max consecutive losses to `backtest/metrics.py`. Small addition, high value for strategy evaluation.

### AVOID for Now

1. **HMM regime detection** — The current SMA slope + ATR spike regime detector is simple, interpretable, and has zero dependencies. HMMs add:
   - `hmmlearn` or `pomegranate` dependency (neither is well-maintained)
   - Stationarity assumptions that crypto violates regularly
   - Fitting/convergence issues (EM algorithm can get stuck)
   - Black-box behavior that's hard to debug when it misclassifies
   - **The actual question is: does the current regime detector cause bad trades?** If not, HMM is premature optimization. If yes, try simpler improvements first: volatility percentile ranking, ADX for trend strength, or a rolling correlation filter. These are more transparent, easier to test, and don't require fitting a latent state model.

2. **Starting over** — The current codebase has:
   - ~5,000+ lines of tested production code
   - 230 tests
   - 7 completed development phases
   - Clean architecture with documented invariants
   - Rebuilding would take 4-8 weeks minimum to reach parity, with zero guarantee of better results.

3. **Complex ML pipeline** — Feature engineering, model training, hyperparameter search. The bot is pre-Phase 6 (live trading). Adding ML complexity before validating the current strategy in paper trading is premature. Get the baseline strategy profitable first, then experiment.

4. **Short selling support** — Noted as a gap, but crypto short selling requires margin accounts, funding rates, and liquidation risk. Not worth the complexity for a retail solo trader at this stage.

---

## Concrete Action Plan (Priority Order)

### P0: Strategy Evaluation Improvements ✅ COMPLETE

1. **Sortino and Calmar ratios** — `src/cryptobot/backtest/metrics.py`, `tests/test_metrics.py` (23 tests)
2. **Max consecutive losses/wins** — `src/cryptobot/backtest/metrics.py`
3. **Walk-forward parameter grid search** — `src/cryptobot/backtest/param_grid.py` (13 tests), `walk_forward.py`

### P1: Allocation Layer ✅ COMPLETE

4. **`src/cryptobot/allocation/` module** — `Allocator` ABC, `FixedRiskAllocator`, `RegimeScaledAllocator` (20 tests).
   `Regime` enum moved to `src/cryptobot/core/regimes.py` to break circular import.
   Wired into `EnsembleStrategy` as injected dependency; `regime_allocation` YAML key for custom factors.

### P2: Backtest Realism ✅ COMPLETE

5. **Size-dependent slippage** — `src/cryptobot/execution/fees.py`: tiered 1×/2×/3× bps by notional.
   Updated `backtest_broker.py` and `paper_broker.py` callers. `tests/test_fees.py` updated.

6. **Gap-through stop handling** — `src/cryptobot/execution/backtest_broker.py`: fills at `bar.open` when
   market gaps through stop price.

### P3: Regime Detection Improvements (do if P0-P2 show strategy needs help)

7. **Only if current regime detector demonstrably misclassifies**: add ADX trend strength filter and volatility percentile ranking to `regime_detector.py`. These are transparent, testable, and don't require ML libraries.

8. **HMM regime detection**: Only pursue if simpler regime improvements (P3.7) fail AND you have >1 year of labeled regime data to validate against. Even then, use it as a research signal alongside the rule-based detector, not as a replacement.

---

## Why NOT Start Over

| Factor | Keep Building | Start Over |
|--------|---------------|------------|
| Time to next backtest run | 0 (works now) | 2-4 weeks |
| Time to paper trading | 0 (works now) | 4-6 weeks |
| Risk rules | 10 tested rules | 0 |
| Test coverage | 230 tests | 0 |
| Config system | Validated, layered | Must rebuild |
| Journal/analytics | Working, with audit trail | Must rebuild |
| Dashboard | 6 pages, working | Must rebuild |
| Bug surface | Known, documented | Unknown |
| Architecture quality | Clean (expert review confirms) | Speculative |

The current bot's architecture is **better than what a YouTube demo typically produces**. The video likely shows a polished demo with good concepts but less production rigor (no risk rules, no kill switch, no test suite, no config validation, no journal audit trail).

---

## Summary

Your instinct is correct. The bot has solid foundations. The two things worth borrowing from the video are (1) walk-forward parameter optimization and (2) explicit allocation layer. Everything else the video shows, your bot already has — often in better form.

Don't chase HMMs. Get your baseline strategy consistently profitable in walk-forward out-of-sample tests first. If the strategy consistently loses in out-of-sample but wins in-sample, the problem is overfitting, not regime detection.
