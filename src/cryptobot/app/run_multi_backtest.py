"""Multi-symbol backtest runner with portfolio-level aggregation.

Runs the same strategy over a basket of symbols independently (one engine
per symbol, equal starting capital per symbol), then aggregates portfolio-
level metrics.

Portfolio equity is modelled as the equal-weight average of per-symbol
normalised return curves (each curve divided by its starting cash).  This is
the standard way to combine independent single-asset backtests into a
portfolio view without modifying the engine.

Usage via CLI:
    cryptobot multi-backtest --config config/backtest_swing_basket.yaml
    cryptobot multi-backtest --config config/backtest_swing_basket.yaml --data-dir data/ --out-dir results/

Data file convention:
    {DATA_DIR}/{SYMBOL_NORMALISED}_{TIMEFRAME}.csv
    SYMBOL_NORMALISED = symbol.replace('/', '_')   e.g. "BTC_USDT"
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from cryptobot.app.run_backtest import _run_symbol, load_bars_from_csv, write_result_bundle
from cryptobot.backtest.engine import BacktestResult
from cryptobot.backtest.metrics import Metrics, _max_drawdown, _sharpe, compute_metrics
from cryptobot.config import load_settings
from cryptobot.core.ids import new_run_id
from cryptobot.monitoring.logging_setup import setup_logging


# ---------------------------------------------------------------------------
# Portfolio metrics
# ---------------------------------------------------------------------------

@dataclass
class SymbolResult:
    symbol: str
    run_id: str
    result: BacktestResult
    bars: list


@dataclass
class PortfolioMetrics:
    total_return: float        # average of per-symbol total returns
    sharpe: float              # Sharpe of portfolio normalised equity curve
    max_drawdown: float        # drawdown of portfolio equity curve
    win_rate: float            # aggregate win rate across all symbols
    n_trades: int              # total trades across all symbols
    trades_per_day: float      # n_trades / total_days
    total_fees: float          # sum of all fees paid
    dominant_symbol: str | None  # symbol contributing > 70% of positive PnL
    dominant_pct: float        # that symbol's share of positive PnL
    avg_pairwise_correlation: float   # mean of off-diagonal correlation coefficients
    correlation_matrix: dict[str, dict[str, float]]  # pairwise correlations
    verdict: str               # promising / mixed / weak


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation coefficient between two equal-length sequences."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _returns_from_equity(equity: list[float]) -> list[float]:
    return [
        (equity[i] - equity[i - 1]) / equity[i - 1]
        for i in range(1, len(equity))
        if equity[i - 1] > 0
    ]


def _portfolio_verdict(m: PortfolioMetrics) -> str:
    ret = m.total_return * 100
    if m.n_trades == 0:
        return "weak — no trades executed across the basket."
    if m.sharpe >= 0.5 and ret > 0 and m.win_rate >= 0.45 and (m.dominant_pct or 0) < 0.70:
        return (
            f"promising — portfolio Sharpe {m.sharpe:.2f}, {ret:+.1f}% avg return, "
            f"{m.win_rate * 100:.0f}% win rate. Continue with walk-forward validation."
        )
    if m.sharpe >= 0.1 or (ret > 0 and m.win_rate >= 0.40):
        msg = f"mixed — portfolio Sharpe {m.sharpe:.2f}, {ret:+.1f}% avg return."
        if (m.dominant_pct or 0) >= 0.70:
            msg += f" One symbol ({m.dominant_symbol}) drives {m.dominant_pct * 100:.0f}% of gains."
        if m.avg_pairwise_correlation > 0.7:
            msg += " High inter-symbol correlation limits diversification benefit."
        msg += " One more validation step needed before proceeding."
        return msg
    return (
        f"weak — portfolio Sharpe {m.sharpe:.2f}, {ret:+.1f}% avg return, "
        f"{m.win_rate * 100:.0f}% win rate. Strategy family not good enough at portfolio level."
    )


def compute_portfolio_metrics(
    symbol_results: list[SymbolResult],
    timeframe: str,
) -> PortfolioMetrics:
    starting_cash = symbol_results[0].result.equity_curve[0]
    min_len = min(len(sr.result.equity_curve) for sr in symbol_results)
    total_days = max(
        (sr.bars[-1].ts_open.date() - sr.bars[0].ts_open.date()).days
        for sr in symbol_results
    )

    # Normalised equity curves (all start at 1.0)
    norm_curves: list[list[float]] = [
        [eq / starting_cash for eq in sr.result.equity_curve[:min_len]]
        for sr in symbol_results
    ]

    # Portfolio equity = equal-weight average of normalised curves, scaled back to dollars
    portfolio_equity = [
        starting_cash * (sum(nc[i] for nc in norm_curves) / len(norm_curves))
        for i in range(min_len)
    ]

    portfolio_sharpe = _sharpe(portfolio_equity, timeframe)
    portfolio_dd = _max_drawdown(portfolio_equity)
    avg_return = sum(
        sr.result.metrics.total_return for sr in symbol_results
    ) / len(symbol_results)

    total_trades = sum(sr.result.metrics.n_trades for sr in symbol_results)
    total_fees = sum(
        sum(float(f.fee) for f in sr.result.fills) for sr in symbol_results
    )
    trades_per_day = round(total_trades / max(total_days, 1), 3)

    # Aggregate win rate
    all_trades_n = total_trades
    all_wins = sum(
        round(sr.result.metrics.hit_rate * sr.result.metrics.n_trades)
        for sr in symbol_results
    )
    win_rate = (all_wins / all_trades_n) if all_trades_n > 0 else 0.0

    # Per-symbol PnL and dominant symbol
    symbol_pnl = {
        sr.symbol: sr.result.final_equity - starting_cash
        for sr in symbol_results
    }
    total_positive_pnl = sum(v for v in symbol_pnl.values() if v > 0)
    dominant_symbol = None
    dominant_pct = 0.0
    if total_positive_pnl > 0:
        for sym, pnl in symbol_pnl.items():
            if pnl > 0:
                share = pnl / total_positive_pnl
                if share > dominant_pct:
                    dominant_pct = share
                    dominant_symbol = sym

    # Correlation matrix from return series
    symbols = [sr.symbol for sr in symbol_results]
    return_series: dict[str, list[float]] = {
        sr.symbol: _returns_from_equity(sr.result.equity_curve[:min_len])
        for sr in symbol_results
    }
    corr_matrix: dict[str, dict[str, float]] = {}
    off_diag: list[float] = []
    for s1 in symbols:
        corr_matrix[s1] = {}
        for s2 in symbols:
            if s1 == s2:
                corr_matrix[s1][s2] = 1.0
            else:
                r1 = return_series[s1]
                r2 = return_series[s2]
                n = min(len(r1), len(r2))
                c = _pearson(r1[:n], r2[:n])
                corr_matrix[s1][s2] = round(c, 3)
                if s2 > s1:  # avoid double-counting
                    off_diag.append(c)
    avg_corr = round(sum(off_diag) / len(off_diag), 3) if off_diag else 0.0

    verdict_placeholder = ""  # filled after construction
    m = PortfolioMetrics(
        total_return=avg_return,
        sharpe=round(portfolio_sharpe, 3),
        max_drawdown=round(portfolio_dd, 3),
        win_rate=round(win_rate, 4),
        n_trades=total_trades,
        trades_per_day=trades_per_day,
        total_fees=round(total_fees, 2),
        dominant_symbol=dominant_symbol,
        dominant_pct=round(dominant_pct, 3),
        avg_pairwise_correlation=avg_corr,
        correlation_matrix=corr_matrix,
        verdict=verdict_placeholder,
    )
    m.verdict = _portfolio_verdict(m)
    return m


# ---------------------------------------------------------------------------
# Portfolio summary writer
# ---------------------------------------------------------------------------

def write_portfolio_summary(
    symbol_results: list[SymbolResult],
    pm: PortfolioMetrics,
    strategy_name: str,
    timeframe: str,
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    starting_cash = symbol_results[0].result.equity_curve[0]

    date_from = min(sr.bars[0].ts_open.strftime("%Y-%m-%d") for sr in symbol_results)
    date_to = max(sr.bars[-1].ts_open.strftime("%Y-%m-%d") for sr in symbol_results)
    basket_str = ", ".join(sr.symbol for sr in symbol_results)

    # ---- portfolio_metrics.json ----
    per_symbol_data = []
    for sr in symbol_results:
        m = sr.result.metrics
        pnl = sr.result.final_equity - starting_cash
        total_fees = sum(float(f.fee) for f in sr.result.fills)
        per_symbol_data.append({
            "symbol": sr.symbol,
            "run_id": sr.run_id,
            "total_return_pct": round(m.total_return * 100, 3),
            "sharpe": round(m.sharpe, 3),
            "max_drawdown_pct": round(m.max_drawdown * 100, 3),
            "win_rate_pct": round(m.hit_rate * 100, 2),
            "n_trades": m.n_trades,
            "pnl": round(pnl, 2),
            "total_fees": round(total_fees, 2),
        })

    portfolio_data = {
        "strategy": strategy_name,
        "timeframe": timeframe,
        "date_from": date_from,
        "date_to": date_to,
        "basket": [sr.symbol for sr in symbol_results],
        "starting_cash_per_symbol": round(starting_cash, 2),
        "portfolio_avg_return_pct": round(pm.total_return * 100, 3),
        "portfolio_sharpe": pm.sharpe,
        "portfolio_max_drawdown_pct": round(pm.max_drawdown * 100, 3),
        "portfolio_win_rate_pct": round(pm.win_rate * 100, 2),
        "total_trades": pm.n_trades,
        "trades_per_day": pm.trades_per_day,
        "total_fees": pm.total_fees,
        "dominant_symbol": pm.dominant_symbol,
        "dominant_pct": round(pm.dominant_pct * 100, 1),
        "avg_pairwise_correlation": pm.avg_pairwise_correlation,
        "correlation_matrix": pm.correlation_matrix,
        "verdict": pm.verdict,
        "per_symbol": per_symbol_data,
    }
    (out_dir / "portfolio_metrics.json").write_text(
        json.dumps(portfolio_data, indent=2)
    )

    # ---- portfolio_summary.md ----
    ret_sign = "+" if pm.total_return >= 0 else ""
    sym_rows = []
    total_positive_pnl = sum(
        sr.result.final_equity - starting_cash
        for sr in symbol_results
        if sr.result.final_equity > starting_cash
    )
    for sr in symbol_results:
        m = sr.result.metrics
        pnl = sr.result.final_equity - starting_cash
        share = ""
        if pnl > 0 and total_positive_pnl > 0:
            share = f"{pnl / total_positive_pnl * 100:.0f}%"
        sym_rows.append(
            f"| {sr.symbol} | {m.total_return * 100:+.1f}% | {m.sharpe:.2f} "
            f"| -{m.max_drawdown * 100:.1f}% | {m.hit_rate * 100:.0f}% "
            f"| {m.n_trades} | ${pnl:+,.0f} | {share} |"
        )

    corr_symbols = [sr.symbol for sr in symbol_results]
    corr_header = "| | " + " | ".join(corr_symbols) + " |"
    corr_sep = "|---|" + "---|" * len(corr_symbols)
    corr_rows = []
    for s1 in corr_symbols:
        vals = " | ".join(f"{pm.correlation_matrix[s1][s2]:.2f}" for s2 in corr_symbols)
        corr_rows.append(f"| {s1} | {vals} |")

    dominant_note = ""
    if pm.dominant_symbol and pm.dominant_pct >= 0.70:
        dominant_note = (
            f"\n**Dominant symbol:** {pm.dominant_symbol} contributes "
            f"{pm.dominant_pct * 100:.0f}% of positive PnL — diversification benefit limited.\n"
        )

    corr_note = ""
    if pm.avg_pairwise_correlation > 0.7:
        corr_note = (
            f"\nAverage pairwise correlation: **{pm.avg_pairwise_correlation:.2f}** — "
            "high correlation, limited diversification benefit.\n"
        )
    elif pm.avg_pairwise_correlation > 0.4:
        corr_note = (
            f"\nAverage pairwise correlation: **{pm.avg_pairwise_correlation:.2f}** — "
            "moderate correlation, some diversification benefit.\n"
        )
    else:
        corr_note = (
            f"\nAverage pairwise correlation: **{pm.avg_pairwise_correlation:.2f}** — "
            "low correlation, meaningful diversification.\n"
        )

    md = f"""\
# Multi-Symbol Backtest: {strategy_name}

> **VERDICT: {pm.verdict}**

**Basket:** {basket_str} | **Timeframe:** {timeframe} | {date_from} -> {date_to}

---

## Portfolio Metrics

| Metric | Value |
|---|---|
| Avg Return (equal weight) | {ret_sign}{pm.total_return * 100:.2f}% |
| Portfolio Sharpe | {pm.sharpe:.2f} |
| Portfolio Max Drawdown | -{pm.max_drawdown * 100:.2f}% |
| Win Rate (all symbols) | {pm.win_rate * 100:.1f}% |
| Total Trades | {pm.n_trades} |
| Avg Trades / Day (portfolio) | {pm.trades_per_day} |
| Total Fees Paid | ${pm.total_fees:,.2f} |

---

## Per-Symbol Breakdown

| Symbol | Return | Sharpe | Max DD | Win Rate | Trades | PnL | Share of Gains |
|---|---|---|---|---|---|---|---|
{chr(10).join(sym_rows)}
{dominant_note}
---

## Correlation (bar returns)

{corr_header}
{corr_sep}
{chr(10).join(corr_rows)}
{corr_note}
---

*Starting cash per symbol: ${starting_cash:,.2f}*
"""
    (out_dir / "portfolio_summary.md").write_text(md)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(
    config_path: str | Path,
    data_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> None:
    settings = load_settings(config_path)
    symbols = settings.run.market.symbols
    timeframe = settings.run.market.timeframe
    sc = settings.run.strategy

    if not symbols:
        raise ValueError("No symbols defined in config market.symbols")

    data_root = Path(data_dir) if data_dir else Path("data")
    out_root = Path(out_dir) if out_dir else None

    # Multi-backtests log at WARNING by default to avoid per-order log spam
    # across 100k+ bars. Results are written to out_dir files instead.
    effective_level = settings.env.log_level if settings.env.log_level != "INFO" else "WARNING"
    log = setup_logging(
        log_dir=settings.env.log_dir,
        level=effective_level,
        run_id="multi-bt",
    )
    log.info(
        "multi_backtest_start",
        strategy=sc.name,
        symbols=symbols,
        timeframe=timeframe,
    )

    symbol_results: list[SymbolResult] = []

    for symbol in symbols:
        normalized = symbol.replace("/", "_")
        data_file = data_root / f"{normalized}_{timeframe}.csv"
        if not data_file.exists():
            raise FileNotFoundError(
                f"Data file not found for {symbol}: {data_file}\n"
                f"Fetch it with: cryptobot fetch-history --symbol {symbol} "
                f"--timeframe {timeframe} --since 2023-01-01\n"
                f"Then export: cryptobot export-history --symbol {symbol} "
                f"--timeframe {timeframe}"
            )

        run_id = new_run_id("bt")
        log.info("loading_bars", symbol=symbol, file=str(data_file))
        bars = load_bars_from_csv(data_file, symbol, timeframe)
        log.info("bars_loaded", symbol=symbol, n=len(bars))

        if len(bars) < 2:
            raise ValueError(f"Too few bars for {symbol}: {len(bars)}")

        result = _run_symbol(settings, bars, symbol, run_id)

        # Journal writes are intentionally skipped for multi-backtest runs.
        # Writing 100k+ equity snapshots per symbol to SQLite makes 15m
        # backtests impractically slow; results are already persisted to
        # per-symbol CSV / JSON files in out_dir.

        symbol_results.append(SymbolResult(symbol=symbol, run_id=run_id, result=result, bars=bars))

        log.info(
            "symbol_complete",
            symbol=symbol,
            run_id=run_id,
            total_return=round(result.metrics.total_return, 4),
            n_trades=result.metrics.n_trades,
        )

        # Per-symbol result bundle
        if out_root is not None:
            sym_dir = out_root / normalized
            write_result_bundle(result, bars, sc.name, sym_dir)

    # Portfolio aggregation
    pm = compute_portfolio_metrics(symbol_results, timeframe)

    # Print summary
    _print_portfolio_report(symbol_results, pm)

    if out_root is not None:
        write_portfolio_summary(symbol_results, pm, sc.name, timeframe, out_root)
        log.info("portfolio_summary_written", out_dir=str(out_root))

    log.info(
        "multi_backtest_complete",
        portfolio_sharpe=pm.sharpe,
        portfolio_return=round(pm.total_return, 4),
        total_trades=pm.n_trades,
        verdict=pm.verdict,
    )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_portfolio_report(
    symbol_results: list[SymbolResult],
    pm: PortfolioMetrics,
) -> None:
    sep = "=" * 60
    thin = "-" * 60

    print(f"\n{sep}")
    print(f"  MULTI-SYMBOL BACKTEST PORTFOLIO SUMMARY")
    print(sep)
    print(f"  Avg return (equal weight) : {pm.total_return * 100:>+10.1f} %")
    print(f"  Portfolio Sharpe          : {pm.sharpe:>+11.2f}")
    print(f"  Portfolio max drawdown    : {pm.max_drawdown * 100:>+10.1f} %")
    print(thin)
    print(f"  Total trades (all symbols): {pm.n_trades:>12d}")
    print(f"  Avg trades / day          : {pm.trades_per_day:>12.3f}")
    print(f"  Win rate (all symbols)    : {pm.win_rate * 100:>11.1f} %")
    print(f"  Total fees paid           : ${pm.total_fees:>11,.2f}")
    print(thin)
    print("  Per-symbol:")
    starting_cash = symbol_results[0].result.equity_curve[0]
    for sr in symbol_results:
        m = sr.result.metrics
        pnl = sr.result.final_equity - starting_cash
        print(
            f"    {sr.symbol:<12}  {m.total_return * 100:>+6.1f}%  "
            f"Sharpe {m.sharpe:>+5.2f}  "
            f"{m.n_trades:>3} trades  PnL ${pnl:>+7,.0f}"
        )
    print(thin)
    print(f"  Avg pairwise correlation  : {pm.avg_pairwise_correlation:>12.2f}")
    if pm.dominant_symbol:
        print(f"  Dominant symbol           : {pm.dominant_symbol} ({pm.dominant_pct * 100:.0f}% of gains)")
    print(thin)
    print(f"  VERDICT: {pm.verdict}")
    print(f"{sep}\n")
