#!/usr/bin/env python3
"""Generate a side-by-side comparison summary for two backtest result bundles.

Usage:
    python scripts/compare_backtests.py <baseline_metrics.json> <new_metrics.json>

Writes a verdict-first Markdown comparison to $GITHUB_STEP_SUMMARY (if set)
and also prints it to stdout.
"""

from __future__ import annotations

import json
import os
import sys


def load(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def pct(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def num(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}"


def dd_cells(a_dd: float, b_dd: float) -> tuple[str, str, str]:
    """Drawdown is positive-stored, displayed as negative. Lower is better."""
    delta = a_dd - b_dd  # positive delta means ADX reduced drawdown (good)
    sign = "+" if delta >= 0 else ""
    return f"-{a_dd:.2f}%", f"-{b_dd:.2f}%", f"{sign}{delta:.2f}pp"


def decision(a: dict, b: dict) -> str:
    sharpe_delta = b["sharpe"] - a["sharpe"]
    if b["sharpe"] >= 0.5 and b["n_trades"] >= 10 and sharpe_delta >= 0.3:
        return (
            "**ADX gate materially improves OOS quality.** "
            "\u2192 Next step: small multi-symbol test."
        )
    if b["sharpe"] > a["sharpe"]:
        return (
            "**ADX gate shows improvement but Sharpe is still below 0.5.** "
            "\u2192 Consider Opening Range Breakout as the next candidate."
        )
    return (
        "**ADX gate does not fix raw Donchian failure.** "
        "\u2192 Next step: Opening Range Breakout."
    )


def build_summary(a: dict, b: dict) -> str:
    a_dd_s, b_dd_s, dd_delta = dd_cells(a["max_drawdown_pct"], b["max_drawdown_pct"])
    sharpe_delta = b["sharpe"] - a["sharpe"]

    lines = [
        "# Donchian + ADX Confirmation \u2014 BTC/USDT 15m",
        "",
        f"> **{b['verdict']}**",
        "",
        f"**Period:** {b['date_from']} \u2192 {b['date_to']}"
        f" | {b['n_bars']:,} bars",
        "",
        "---",
        "",
        "## Verdict",
        "",
        "| Strategy | Verdict |",
        "|---|---|",
        f"| Raw Donchian | {a['verdict']} |",
        f"| Donchian + ADX (gate \u2265 25) | {b['verdict']} |",
        "",
        "---",
        "",
        "## Side-by-Side",
        "",
        "| Metric | Raw Donchian | Donchian + ADX | \u0394 |",
        "|---|---|---|---|",
        f"| Total Return | {pct(a['total_return_pct'])} | {pct(b['total_return_pct'])}"
        f" | {pct(b['total_return_pct'] - a['total_return_pct'])} |",
        f"| Max Drawdown | {a_dd_s} | {b_dd_s} | {dd_delta} |",
        f"| Sharpe | {a['sharpe']:.2f} | {b['sharpe']:.2f} | {num(sharpe_delta)} |",
        f"| Sortino | {a['sortino']:.2f} | {b['sortino']:.2f}"
        f" | {num(b['sortino'] - a['sortino'])} |",
        f"| Trades | {a['n_trades']} | {b['n_trades']}"
        f" | {num(b['n_trades'] - a['n_trades'])} |",
        f"| Trades / Day | {a['trades_per_day']} | {b['trades_per_day']}"
        f" | {num(b['trades_per_day'] - a['trades_per_day'])} |",
        f"| Win Rate | {a['win_rate_pct']:.1f}% | {b['win_rate_pct']:.1f}%"
        f" | {pct(b['win_rate_pct'] - a['win_rate_pct'])} |",
        f"| Expectancy | ${a['expectancy_dollars']:,.2f} | ${b['expectancy_dollars']:,.2f}"
        f" | ${b['expectancy_dollars'] - a['expectancy_dollars']:,.2f} |",
        f"| Profit Factor | {a['profit_factor']:.2f} | {b['profit_factor']:.2f}"
        f" | {num(b['profit_factor'] - a['profit_factor'])} |",
        f"| Total Fees | ${a['total_fees']:,.2f} | ${b['total_fees']:,.2f}"
        f" | ${b['total_fees'] - a['total_fees']:,.2f} |",
        "",
        "---",
        "",
        "## Decision",
        "",
        decision(a, b),
        "",
        "> 4h SMA remains parked as a separate swing-mode candidate.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <baseline_metrics.json> <new_metrics.json>", file=sys.stderr)
        sys.exit(2)

    a = load(sys.argv[1])
    b = load(sys.argv[2])

    if a is None or b is None:
        missing = sys.argv[1] if a is None else sys.argv[2]
        msg = (
            "## Donchian ADX Test\n\n"
            f"> One or both metrics files not found: `{missing}`"
            " — backtest may have failed.\n"
        )
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a") as f:
                f.write(msg)
        print(msg)
        sys.exit(1)

    md = build_summary(a, b)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(md)

    print(md)


if __name__ == "__main__":
    main()
