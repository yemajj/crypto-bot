# Paper Trading — Recommendations to Increase Trade Volume

## Context

The goal is to review paper-trading results faster. The current `config/paper.yaml` is tuned for conservative Phase 4 paper trading and produces very few trades because of a combination of slow SMA windows, a long warmup, and strict circuit-breaker-style risk caps. This document identifies the minimum set of changes that will materially increase trade activity without turning the output into noise that is hard to interpret. No code or config changes have been made — these are recommendations only.

## Current State (what's suppressing trades)

Baseline: `config/paper.yaml`
- Symbol: BTC/USDT only
- Timeframe: 5m
- Strategy: `sma_crossover`, fast=20, slow=50 (slow trend-follower; crossovers are rare)
- `warmup_bars: 200` → ~16.7 hours of silent startup on 5m bars before the first trade is even possible
- `max_open_positions: 1` → after one entry, nothing else can fire until the exit
- `max_daily_loss_pct: 0.01` → **one losing trade at the 0.5% risk budget plus fees/slippage can nearly trip the daily circuit breaker**; two losses almost certainly will
- `cooldown_after_losses: 3`, `cooldown_bars: 4` → after 3 losses, no entries for 20 min
- `max_orders_per_minute: 5` → not the bottleneck today, but relevant if we speed things up

Already existing alternate configs (no new code needed to try them):
- `config/paper_fast.yaml` — SMA(5,15), `warmup_bars: 30`. Same risk caps as paper.yaml.
- `config/paper_rsi.yaml` — `rsi` mean-reversion strategy, `warmup_bars: 30`. Same risk caps.
- `config/ensemble.yaml` — 1h timeframe, multi-strategy agreement required. **Lower** trade frequency, not higher.

Relevant source files:
- `config/paper.yaml` — baseline
- `config/paper_fast.yaml`, `config/paper_rsi.yaml` — existing short-warmup alternatives
- `src/cryptobot/strategy/sma_crossover.py` (signal logic, lines 59–136)
- `src/cryptobot/strategy/rsi.py` (scoring, lines 43–69)
- `src/cryptobot/risk/rules.py` (all suppressors: MaxDailyLoss L112–126, MaxOpenPositions L141–165, CooldownAfterLoss L168–192, MaxOrdersPerMinute L78–89)
- `src/cryptobot/app/run_paper.py` (run loop, warmup handling L292–294, strategy call L307)
- `src/cryptobot/config/settings.py` (cross-field validation L58–174; sma_crossover requires fast<slow)

---

## Ranked Recommendations

Ranked by **trade-count uplift per unit of added confusion**, for the stated goal.

### 1. Switch to `paper_fast.yaml` (SMA 5/15, warmup 30) — BIGGEST EASY WIN
- **Why it increases trades:** Crossovers on a 5/15 SMA fire roughly 3–5× more often than 20/50 on the same 5m bars. Warmup drops from 200→30 bars, so the first possible trade is ~30 min after start instead of ~16.7 hours.
- **Main downside:** Noisier crossovers → more whipsaws in ranging markets, lower win rate, more fee drag.
- **Testing only or longer-term?** Primarily testing/iteration. Not recommended as a long-term production configuration, but still a legitimate "fast trend-follower" variant. Same strategy class as baseline, so results are directly comparable.

### 2. Loosen the daily-loss circuit breaker and cooldown (testing only)
- **Why it increases trades:** Today, `max_daily_loss_pct: 0.01` is the single biggest reason the bot "goes quiet" after you start watching it — a bad trade plus fees + slippage can lock out the rest of the day. `cooldown_after_losses: 3` with `cooldown_bars: 4` adds another 20-minute freeze on top. Raising these lets the bot keep trading through normal drawdowns so you actually see meaningful sample sizes.
- **Suggested deltas (testing):** `max_daily_loss_pct: 0.05`, `cooldown_after_losses: 5` (or `0`), `cooldown_bars: 2`.
- **Main downside:** Bigger potential daily drawdown during the review window. You're deliberately removing the safety net, so only do this on paper.
- **Testing only or longer-term?** **Testing only.** Restore the tight caps before anything resembling production.

### 3. Run `paper_rsi.yaml` (RSI mean-reversion) as a second, independent stream
- **Why it increases trades:** RSI and SMA-crossover generate signals on orthogonal conditions. RSI fires on 30/70 threshold crosses, which happens often in chop — exactly when SMA-crossover is silent. You effectively double the signal rate by running both, not by combining them.
- **Main downside:** You now have two PnL streams to read. Keep them in separate runs/journals so you can tell them apart.
- **Testing only or longer-term?** Useful both ways. Good for testing behavioral diversity now; could be a real portfolio diversifier later.

### 4. Add a 1m-timeframe "hyper-fast" config (new file)
- **Why it increases trades:** 5× more bars per hour → proportionally more signals. Warmup of 30 bars on 1m = 30 min to first possible trade.
- **Main downside:** Fees/slippage dominate on 1m (taker 10 bps + slip 5 bps = 15 bps round-trip drag); heavy noise; you will also start bumping into `max_orders_per_minute: 5`. Expect to raise that too.
- **Testing only or longer-term?** **Testing only.** Not viable long-term at current fee assumptions.

### 5. Expand the symbol allow-list (e.g. BTC/USDT + ETH/USDT + SOL/USDT)
- **Why it increases trades:** Linear multiplier — N symbols gives you ~N× signal rate, all else equal.
- **Main downside:** You must also raise `max_open_positions` (2–5) and widen `symbol_allow_list`. Results become slightly harder to read because trades are interleaved across symbols. Correlated entries (all three BTC-like pairs at once) can also eat your gross-exposure budget.
- **Testing only or longer-term?** Good for both. This is a legitimate production direction once you trust the single-symbol behavior.

### 6. Reduce `warmup_bars` on the baseline config
- **Why it increases trades:** Purely eliminates startup dead time. 50 bars is plenty to seed SMA(20,50) + ATR(14).
- **Main downside:** The first few bars after warmup have slightly less settled indicators. Negligible impact.
- **Testing only or longer-term?** Safe for both. There's no reason to keep 200 bars on 5m.

### 7. Raise `max_orders_per_minute`
- **Why it increases trades:** Removes a rate ceiling. On 5m bars you almost never hit it today, but once you combine faster timeframes + multi-symbol + loosened caps it becomes the next bottleneck.
- **Main downside:** None in paper mode.
- **Testing only or longer-term?** Safe for both.

### Keep strategies separate, don't "combine" via the ensemble
The existing `ensemble.yaml` **reduces** trade count for two reasons: (a) it runs on 1h bars, and (b) it requires `min_agreeing_buckets: 2` out of 4 scoring strategies to agree above magnitude 0.25 before firing. That's the opposite of the goal. For more activity, **run the strategies as separate paper sessions** (different configs, different run_ids in the journal) and compare. Combining should be revisited only once you actually want signal filtering rather than signal amplification.

---

## Deliverables

### 1. Top 3 next tests

1. **`paper_fast.yaml` as-is** — instant baseline for "how many more signals does SMA(5,15) produce on 5m?" Zero new files.
2. **`paper_fast_open.yaml` (new)** — `paper_fast.yaml` + loosened circuit breakers so the signals aren't smothered by the 1% daily loss cap. This is the one that actually lets you see a meaningful sample size in a single session.
3. **`paper_rsi.yaml` as-is** — orthogonal strategy to confirm that most of the uplift is strategy-specific rather than generic-market-driven. Run in a second shell alongside test #2.

### 2. Exact config changes suggested

**No edits to existing files.** Add one new file:

`config/paper_fast_open.yaml` (copy of `paper_fast.yaml` with the risk block loosened and a comment explaining it's testing-only):

```yaml
# Testing-only paper config — SMA(5,15) + loosened circuit breakers.
# Purpose: generate the maximum number of SMA crossover trades in a single
# paper session so results can be reviewed quickly. NOT for production paper
# trading — tight caps must be restored before Phase 5 sign-off.

mode: paper

market:
  symbols:
    - BTC/USDT
  timeframe: 5m

strategy:
  name: sma_crossover
  params:
    fast: 5
    slow: 15
    risk_per_trade_pct: 0.005
    atr_window: 14

risk:
  max_position_pct: 0.05
  max_gross_exposure_pct: 0.25
  max_daily_loss_pct: 0.05          # was 0.01 — don't let one bad trade freeze the session
  max_orders_per_minute: 15         # was 5 — avoid rate-limit backpressure on faster configs
  require_stop_loss: true
  symbol_allow_list:
    - BTC/USDT
  max_open_positions: 1
  cooldown_after_losses: 5          # was 3 — loss-streak pause is less aggressive
  cooldown_bars: 2                  # was 4 — shorter pause when it does trigger

fees:
  taker_bps: 10.0
  maker_bps: 5.0
  slippage_bps: 5.0

starting_cash: 10000.0
warmup_bars: 30
poll_interval_seconds: 30.0
```

Other suggested configs (later, only if/when wanted):
- `config/paper_fast_multi.yaml` — same as above but `symbols: [BTC/USDT, ETH/USDT, SOL/USDT]`, `max_open_positions: 3`, and an expanded `symbol_allow_list`. (Recommendation #5.)
- `config/paper_1m.yaml` — copy of `paper_fast_open.yaml` with `timeframe: 1m`, `warmup_bars: 60`. (Recommendation #4, testing-only.)

### 3. The single best next test to run first

**Create `config/paper_fast_open.yaml` (as specified above) and run `cryptobot paper --config config/paper_fast_open.yaml`.**

Rationale: it's a single new file, leaves every existing config untouched, produces the biggest trade-count uplift per change, and — crucially — it's the only one of the three top options that prevents the daily-loss circuit breaker from stopping the experiment halfway through. After one session the contribution of faster SMA windows vs loosened caps can be isolated, and the next step (RSI or multi-symbol) can be chosen accordingly.

---

## Verification (how to tell it worked)

After running the first test:
- Count `run_start`/`order`/`fill` rows in the journal for the new run_id (`data/cryptobot.sqlite`) — expect multiples more fills than an equivalent-duration `paper.yaml` session.
- Watch the run_paper log for `intent_rejected` lines. If rejections are dominated by `MaxDailyLoss` or `CooldownAfterLoss`, the caps need to be loosened further. If dominated by `MaxOpenPositions`, that's expected and means signals are firing faster than exits.
- Confirm the first trade appears within ~30 min of startup (proof the `warmup_bars` change took effect).
- If rejections are dominated by `MaxOrdersPerMinute`, raise it further before the next run.
- Keep each test in its own shell so journal `run_id`s don't mix, and compare side-by-side.
