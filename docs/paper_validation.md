# Paper Validation Runbook

This document is the source of truth for paper-trading validation before Phase 6 live trading.

## Purpose

Use paper validation to answer one question: does the bot behave safely and consistently enough to justify writing live-trading code?

Validation runs are:

- long-running paper sessions using `config/paper_validation.yaml`
- journaled to SQLite like any other paper run
- tagged automatically so `cryptobot validate-paper` can summarize them

Development runs using `config/paper_fast.yaml` are useful for signal verification, but they do **not** count toward the live-trading gate.

## Standard Workflow

```bash
# 1. Preload history once
cryptobot fetch-history --symbol BTC/USDT --timeframe 5m --since 2024-01-01

# 2. Start a validation paper run
cryptobot paper --config config/paper_validation.yaml

# 3. Review the most recent run in detail
cryptobot report

# 4. Review readiness across validation runs
cryptobot validate-paper
cryptobot validate-paper --days 28
```

If you want to inspect specific completed paper runs:

```bash
cryptobot validate-paper --run-id paper_abc123 --run-id paper_def456
```

## Manual Checks

Automated reporting is not enough. Before Phase 6, manually verify all of the following:

1. Kill switch works: create `CRYPTOBOT_KILL_SWITCH_FILE` while a validation run is active and confirm the loop halts within one bar.
2. Daily loss halt works: use a deliberately aggressive scenario or controlled test conditions and confirm new entries stop after the configured daily loss cap is hit.
3. Telegram alerts are reliable: confirm start, stop, fill, stop-loss, and daily-loss-cap alerts arrive when expected.
4. Paper behavior matches backtest expectations closely enough that differences look like market conditions and execution assumptions, not logic bugs.

## Interpreting `validate-paper`

`cryptobot validate-paper` summarizes completed validation runs only. By default it looks back 14 days and ignores untagged paper runs.

The automated gate mirrors the per-run report thresholds:

- Sharpe > 1.0
- Max drawdown < 20%
- at least 30 closed trades
- win rate > 40%
- fees < 15% of gross profit

The output is split into:

- automated checks derived from journaled runs
- manual checks that stay `PENDING` until you review them yourself
- contributing run IDs so you can drill into any run with `cryptobot report --run-id ...`

## Exit Criteria For Phase 6

Do not start live-trading implementation until all of the following are true:

1. `cryptobot validate-paper` shows the automated checks passing over a meaningful multi-run window.
2. Weeks of validation logs have been reviewed without finding systematic trading or risk bugs.
3. Manual kill-switch, daily-loss, and Telegram checks are complete.
4. Paper-vs-backtest differences are understood and considered acceptable.
