"""Entry point for backtest runs.

Usage via CLI:
    cryptobot backtest --config config/backtest.yaml --data data/BTCUSDT_1h.csv

Usage directly:
    from cryptobot.app.run_backtest import main
    main("config/backtest.yaml", "data/BTCUSDT_1h.csv")

Data file format (CSV, Binance-compatible):
    open_time,open,high,low,close,volume
    open_time may be a Unix timestamp in ms or an ISO-8601 datetime string.
    Lines starting with '#' and blank lines are ignored.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from cryptobot.backtest.engine import BacktestEngine, BacktestResult
from cryptobot.backtest.metrics import Metrics
from cryptobot.config import load_settings
from cryptobot.core.ids import new_run_id
from cryptobot.core.types import Bar
from cryptobot.execution.backtest_broker import BacktestBroker
from cryptobot.execution.fees import FeeModel
from cryptobot.journal.writer import (
    build_engine as build_db_engine,
    init_db,
    make_session_factory,
    record_equity_snapshots_bulk,
    record_fill,
    record_order,
    record_run_end,
    record_run_start,
)
from cryptobot.monitoring.logging_setup import setup_logging
from cryptobot.risk.manager import RiskManager
from cryptobot.risk.rules import (
    KillSwitchFile,
    MaxDailyLoss,
    MaxGrossExposurePct,
    MaxOrdersPerMinute,
    MaxPositionSizePct,
    OneOrderPerSymbolInFlight,
    RequireStopLoss,
    SymbolAllowList,
)
from cryptobot.strategy.registry import get_strategy


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

def load_bars_from_csv(
    path: Path,
    symbol: str,
    timeframe: str,
) -> list[Bar]:
    """Parse a OHLCV CSV file into a sorted list of Bar objects.

    Accepted column orders:
      open_time, open, high, low, close, volume  (Binance export)
      timestamp, open, high, low, close, volume

    open_time / timestamp may be:
      - a Unix timestamp in milliseconds (integer or float)
      - an ISO-8601 datetime string (e.g. "2023-01-01 00:00:00")
    """
    bars: list[Bar] = []
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    with path.open(newline="", encoding="utf-8") as f:
        # Skip comment lines, find header.
        lines = [ln for ln in f if not ln.startswith("#") and ln.strip()]

    reader = csv.DictReader(lines)
    if reader.fieldnames is None:
        raise ValueError(f"CSV {path} appears to have no header row")

    fieldnames_lower = [fn.lower().strip() for fn in reader.fieldnames]
    ts_col = next(
        (fn for fn in fieldnames_lower if fn in ("open_time", "timestamp", "date", "time")),
        fieldnames_lower[0],
    )

    for row in reader:
        row_lower = {k.lower().strip(): v for k, v in row.items()}
        raw_ts = row_lower.get(ts_col, "").strip()
        if not raw_ts:
            continue
        ts = _parse_timestamp(raw_ts)

        try:
            bar = Bar(
                symbol=symbol,
                timeframe=timeframe,
                ts_open=ts,
                open=Decimal(row_lower["open"].strip()),
                high=Decimal(row_lower["high"].strip()),
                low=Decimal(row_lower["low"].strip()),
                close=Decimal(row_lower["close"].strip()),
                volume=Decimal(row_lower.get("volume", "0").strip() or "0"),
            )
        except (InvalidOperation, KeyError) as exc:
            raise ValueError(f"Bad row in {path}: {row} — {exc}") from exc

        bars.append(bar)

    bars.sort(key=lambda b: b.ts_open)
    return bars


def _parse_timestamp(raw: str) -> datetime:
    """Parse a timestamp that may be ms-epoch or ISO-8601."""
    # Try numeric (Unix ms).
    try:
        ms = float(raw)
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    except ValueError:
        pass
    # Try ISO-8601 (strip timezone suffix so strptime can parse it).
    raw_naive = raw.split("+")[0].strip() if "+" in raw else raw.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw_naive, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: {raw!r}")


# ---------------------------------------------------------------------------
# Result bundle writer
# ---------------------------------------------------------------------------

def write_result_bundle(
    result: BacktestResult,
    bars: list[Bar],
    strategy_name: str,
    out_dir: Path,
) -> None:
    """Write metrics.json, trades.csv, and summary.md to out_dir.

    Designed for artifact upload — gives a phone-readable summary of every run
    without needing to open the SQLite journal.
    """
    import csv as _csv
    import json

    out_dir.mkdir(parents=True, exist_ok=True)

    m = result.metrics
    symbol = bars[0].symbol
    timeframe = bars[0].timeframe
    date_from = bars[0].ts_open.strftime("%Y-%m-%d")
    date_to = bars[-1].ts_open.strftime("%Y-%m-%d")
    starting = result.equity_curve[0]
    total_fees = sum(float(f.fee) for f in result.fills)

    # ---- metrics.json ----
    metrics_data = {
        "run_id": result.run_id,
        "strategy": strategy_name,
        "symbol": symbol,
        "timeframe": timeframe,
        "date_from": date_from,
        "date_to": date_to,
        "n_bars": result.n_bars,
        "starting_cash": round(starting, 2),
        "final_equity": round(result.final_equity, 2),
        "total_return_pct": round(m.total_return * 100, 3),
        "max_drawdown_pct": round(m.max_drawdown * 100, 3),
        "sharpe": round(m.sharpe, 4),
        "sortino": round(m.sortino, 4),
        "calmar": round(m.calmar, 4),
        "profit_factor": round(m.profit_factor, 4),
        "win_rate_pct": round(m.hit_rate * 100, 2),
        "n_trades": m.n_trades,
        "avg_trade_return_pct": round(m.avg_trade_return * 100, 4),
        "time_in_market_pct": round(m.time_in_market_pct, 2),
        "max_consecutive_losses": m.max_consecutive_losses,
        "max_consecutive_wins": m.max_consecutive_wins,
        "total_fees": round(total_fees, 2),
    }
    (out_dir / "metrics.json").write_text(json.dumps(metrics_data, indent=2))

    # ---- trades.csv ----
    # Build side lookup from strategy-submitted orders; stop-loss fills won't
    # match (they're broker-internal) and are labelled "stop_sell".
    order_side = {o.order_id: o.side.value for o in result.orders}
    with (out_dir / "trades.csv").open("w", newline="", encoding="utf-8") as f:
        writer = _csv.writer(f)
        writer.writerow(["ts", "symbol", "side", "price", "qty", "fee", "fee_currency"])
        for fill in result.fills:
            side = order_side.get(fill.order_id, "stop_sell")
            writer.writerow([
                fill.ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                symbol,
                side,
                str(fill.price),
                str(fill.qty),
                str(fill.fee),
                fill.fee_currency,
            ])

    # ---- summary.md ----
    ret_sign = "+" if m.total_return >= 0 else ""
    avg_sign = "+" if m.avg_trade_return >= 0 else ""
    md = f"""\
# Backtest Summary

**Run ID:** `{result.run_id}`
**Strategy:** {strategy_name}
**Symbol:** {symbol} {timeframe}
**Period:** {date_from} → {date_to} ({result.n_bars:,} bars)
**Starting cash:** ${starting:,.2f}

## Returns

| Metric | Value |
|---|---|
| Final Equity | ${result.final_equity:,.2f} |
| Total Return | {ret_sign}{m.total_return * 100:.2f}% |
| Max Drawdown | -{m.max_drawdown * 100:.2f}% |
| Sharpe Ratio | {m.sharpe:.2f} |
| Sortino Ratio | {m.sortino:.2f} |
| Calmar Ratio | {m.calmar:.2f} |

## Trading

| Metric | Value |
|---|---|
| Closed Trades | {m.n_trades} |
| Win Rate | {m.hit_rate * 100:.1f}% |
| Profit Factor | {m.profit_factor:.2f} |
| Avg Trade Return | {avg_sign}{m.avg_trade_return * 100:.3f}% |
| Time in Market | {m.time_in_market_pct:.1f}% |
| Max Consec. Losses | {m.max_consecutive_losses} |
| Max Consec. Wins | {m.max_consecutive_wins} |
| Total Fees Paid | ${total_fees:,.2f} |
"""
    (out_dir / "summary.md").write_text(md)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(config_path: str | Path, data_path: str | Path | None = None, out_dir: Path | None = None) -> str:
    settings = load_settings(config_path)
    run_id = new_run_id("bt")
    log = setup_logging(
        log_dir=settings.env.log_dir,
        level=settings.env.log_level,
        run_id=run_id,
    )
    log.info(
        "backtest_start",
        run_id=run_id,
        config=str(config_path),
        strategy=settings.run.strategy.name,
        symbols=settings.run.market.symbols,
    )

    if data_path is None:
        log.error("backtest_no_data", hint="pass --data <csv-file>")
        raise ValueError(
            "No data file provided. Pass --data path/to/OHLCV.csv or supply "
            "data_path= to main()."
        )

    symbol = settings.run.market.symbols[0]
    timeframe = settings.run.market.timeframe

    log.info("loading_bars", file=str(data_path), symbol=symbol, timeframe=timeframe)
    bars = load_bars_from_csv(Path(data_path), symbol, timeframe)
    log.info("bars_loaded", n=len(bars))

    if len(bars) < 2:
        raise ValueError(f"Need at least 2 bars, got {len(bars)} from {data_path}")

    # --- Build components ---
    rc = settings.run.risk
    fc = settings.run.fees
    sc = settings.run.strategy

    fees = FeeModel(
        taker_bps=fc.taker_bps,
        maker_bps=fc.maker_bps,
        slippage_bps=fc.slippage_bps,
    )

    starting_cash = 10_000.0  # default; will expose as config field in Phase 4+

    broker = BacktestBroker(starting_cash=starting_cash, fees=fees)

    rules = [
        SymbolAllowList(rc.symbol_allow_list),
        MaxOrdersPerMinute(rc.max_orders_per_minute),
        OneOrderPerSymbolInFlight(),
    ]
    if rc.require_stop_loss:
        rules.append(RequireStopLoss())
    rules += [
        MaxDailyLoss(max_loss_pct=rc.max_daily_loss_pct),
        MaxPositionSizePct(rc.max_position_pct),
        MaxGrossExposurePct(rc.max_gross_exposure_pct),
        KillSwitchFile(settings.env.kill_switch_file),
    ]
    risk = RiskManager(rules)

    strategy_cls = get_strategy(sc.name)
    strategy = strategy_cls(params=sc.params)

    engine = BacktestEngine(strategy=strategy, broker=broker, risk=risk, bars=bars)

    log.info("backtest_running", n_bars=len(bars))
    result = engine.run(run_id)

    # --- Journal writes ---
    init_db(settings.env.db_url)
    db_engine = build_db_engine(settings.env.db_url)
    sf = make_session_factory(db_engine)
    record_run_start(sf, run_id, mode="backtest", strategy_name=sc.name,
                     notes=f"starting_cash={starting_cash:.2f}")
    for order in result.orders:
        record_order(sf, run_id, order)
    for fill in result.fills:
        record_fill(sf, fill)
    record_equity_snapshots_bulk(sf, run_id, [b.ts_open for b in bars], result.equity_curve)
    record_run_end(sf, run_id, notes=f"final_equity={result.final_equity:.2f}")

    _print_report(result, symbol, timeframe, bars, settings.run.fees.taker_bps)

    if out_dir is not None:
        write_result_bundle(result, bars, sc.name, Path(out_dir))
        log.info("result_bundle_written", out_dir=str(out_dir))

    log.info(
        "backtest_complete",
        run_id=run_id,
        total_return=round(result.metrics.total_return, 4),
        n_trades=result.metrics.n_trades,
    )

    return run_id


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_report(
    result: BacktestResult,
    symbol: str,
    timeframe: str,
    bars: list[Bar],
    taker_bps: float,
) -> None:
    m: Metrics = result.metrics
    start_ts = bars[0].ts_open.strftime("%Y-%m-%d")
    end_ts = bars[-1].ts_open.strftime("%Y-%m-%d")
    starting = result.equity_curve[0]

    total_fees = sum(float(f.fee) for f in result.fills)

    sep = "━" * 51
    thin = "─" * 51

    print(f"\n{sep}")
    print(f"  BACKTEST  {result.run_id}")
    print(f"  {symbol}  {timeframe}  |  {start_ts} → {end_ts}")
    print(sep)
    print(f"  Starting capital : ${starting:>12,.2f}")
    print(f"  Final equity     : ${result.final_equity:>12,.2f}")
    print(f"  Total return     : {m.total_return * 100:>+11.1f} %")
    print(f"  Max drawdown     : {-m.max_drawdown * 100:>+11.1f} %")
    print(f"  Sharpe ratio     : {m.sharpe:>12.2f}")
    print(thin)
    print(f"  Closed trades    : {m.n_trades:>12d}")
    print(f"  Win rate         : {m.hit_rate * 100:>11.1f} %")
    print(f"  Profit factor    : {m.profit_factor:>12.2f}")
    print(f"  Avg trade return : {m.avg_trade_return * 100:>+11.2f} %")
    print(f"  Time in market   : {m.time_in_market_pct:>11.1f} %")
    if total_fees > 0:
        print(thin)
        print(f"  Total fees paid  : ${total_fees:>12,.2f}")
    print(f"{sep}\n")
