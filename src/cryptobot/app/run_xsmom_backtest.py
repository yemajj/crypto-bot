"""Cross-sectional XSMOM multi-symbol backtest runner.

Unlike run_multi_backtest.py (which creates one strategy instance per symbol),
this runner uses a SINGLE shared strategy instance across all symbols.  That
allows CrossSectionalMomentumStrategy._roc_cache to accumulate returns for
every symbol in the basket, enabling genuine cross-sectional ranking at each
time step.

Execution model
---------------
All bars from all symbols are merged into one timeline sorted by ts_open.
For each (timestamp, symbol, bar) triple the runner:
  1. Settles any pending fills at bar.open (no look-ahead).
  2. Checks stop-loss triggers.
  3. Snapshots equity at bar.close.
  4. Calls strategy.on_bar() — the shared strategy updates _roc_cache and
     returns Intents based on cross-sectional rank.
  5. Risk-checks each Intent and submits approved orders to the per-symbol
     broker.

Risk rules applied: SymbolAllowList, MaxOrdersPerMinute,
OneOrderPerSymbolInFlight, RequireStopLoss, MaxDailyLoss, MaxPositionSizePct,
MaxGrossExposurePct, MaxOpenPositions, KillSwitchFile.

MaxOpenPositions receives the combined open-position count across all symbols
(portfolio-level), so a single cap governs the whole basket.

Note: CooldownAfterLoss is not implemented in this runner (tracking consecutive
losses across a shared timeline is non-trivial).  Set cooldown_after_losses=0
in the risk config to keep behaviour predictable.

Usage
-----
    python -m cryptobot.app.run_xsmom_backtest \\
        config/backtest_xsmom.yaml --data-dir data/ --out-dir results/xsmom/
"""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from cryptobot.app.run_backtest import load_bars_from_csv, write_result_bundle
from cryptobot.app.run_multi_backtest import (
    SymbolResult,
    _print_portfolio_report,
    compute_portfolio_metrics,
    write_portfolio_summary,
)
from cryptobot.backtest.engine import BacktestResult
from cryptobot.backtest.metrics import ClosedTrade, compute_metrics
from cryptobot.config import load_settings
from cryptobot.core.ids import new_order_id, new_run_id
from cryptobot.core.types import (
    Bar,
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
)
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
    MaxOpenPositions,
    MaxOrdersPerMinute,
    MaxPositionSizePct,
    OneOrderPerSymbolInFlight,
    RequireStopLoss,
    SymbolAllowList,
)
from cryptobot.risk.state_builder import build_risk_state
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.registry import get_strategy


# ---------------------------------------------------------------------------
# Internal trade tracking
# ---------------------------------------------------------------------------

class _Entry(NamedTuple):
    """Tracks an open entry for PnL calculation on close."""
    price: float
    qty: float
    fee: float
    bar_idx: int


def _update_trades(
    sym: str,
    pos_before: Position | None,
    pos_after: Position | None,
    fills: list[Fill],
    bar_idx: int,
    open_entries: dict[str, _Entry | None],
    closed_trades: dict[str, list[ClosedTrade]],
) -> None:
    """Detect position open/close events and record closed trades."""
    if not fills:
        return
    before_qty = pos_before.qty if pos_before else Decimal("0")
    after_qty = pos_after.qty if pos_after else Decimal("0")

    if before_qty == Decimal("0") and after_qty > Decimal("0"):
        # Position opened.
        f = fills[0]
        open_entries[sym] = _Entry(float(f.price), float(f.qty), float(f.fee), bar_idx)

    elif before_qty > Decimal("0") and after_qty == Decimal("0"):
        # Position closed.
        entry = open_entries[sym]
        if entry is not None:
            f = fills[-1]
            pnl = (float(f.price) - entry.price) * entry.qty - entry.fee - float(f.fee)
            closed_trades[sym].append(ClosedTrade(
                symbol=sym,
                entry_price=entry.price,
                exit_price=float(f.price),
                qty=entry.qty,
                pnl=pnl,
                n_bars=bar_idx - entry.bar_idx,
            ))
        open_entries[sym] = None


# ---------------------------------------------------------------------------
# Risk manager factory
# ---------------------------------------------------------------------------

def _build_risk(settings: object, symbols: list[str]) -> RiskManager:
    rc = settings.run.risk  # type: ignore[attr-defined]
    rules = [
        SymbolAllowList(list(symbols)),
        MaxOrdersPerMinute(rc.max_orders_per_minute),
        OneOrderPerSymbolInFlight(),
    ]
    if rc.require_stop_loss:
        rules.append(RequireStopLoss())
    rules += [
        MaxDailyLoss(max_loss_pct=rc.max_daily_loss_pct),
        MaxPositionSizePct(rc.max_position_pct),
        MaxGrossExposurePct(rc.max_gross_exposure_pct),
        MaxOpenPositions(rc.max_open_positions),
        KillSwitchFile(settings.env.kill_switch_file),  # type: ignore[attr-defined]
    ]
    return RiskManager(rules)


# ---------------------------------------------------------------------------
# Portfolio runner
# ---------------------------------------------------------------------------

def _run_portfolio(
    settings: object,
    all_bars: dict[str, list[Bar]],
    run_ids: dict[str, str],
) -> list[SymbolResult]:
    """Core multi-symbol loop with a shared strategy instance.

    Returns one SymbolResult per symbol for downstream aggregation.
    """
    sc = settings.run.strategy  # type: ignore[attr-defined]
    fc = settings.run.fees  # type: ignore[attr-defined]
    symbols = list(all_bars.keys())
    starting_cash: float = settings.run.starting_cash  # type: ignore[attr-defined]

    fees = FeeModel(
        taker_bps=fc.taker_bps,
        maker_bps=fc.maker_bps,
        slippage_bps=fc.slippage_bps,
    )

    # ONE shared strategy instance — enables cross-sectional _roc_cache.
    strategy_cls = get_strategy(sc.name)
    strategy = strategy_cls(params=sc.params)

    risk = _build_risk(settings, symbols)

    # Per-symbol state.
    brokers: dict[str, BacktestBroker] = {
        sym: BacktestBroker(starting_cash, fees) for sym in symbols
    }
    histories: dict[str, list[Bar]] = {sym: [] for sym in symbols}
    equity_curves: dict[str, list[float]] = {sym: [starting_cash] for sym in symbols}
    submitted_orders: dict[str, list[Order]] = {sym: [] for sym in symbols}
    closed_trades: dict[str, list[ClosedTrade]] = {sym: [] for sym in symbols}
    bars_in_position: dict[str, int] = {sym: 0 for sym in symbols}
    open_entries: dict[str, _Entry | None] = {sym: None for sym in symbols}
    bar_counts: dict[str, int] = {sym: 0 for sym in symbols}
    last_bars: dict[str, Bar | None] = {sym: None for sym in symbols}

    day_start_equity: dict[str, float] = {sym: starting_cash for sym in symbols}
    current_day: dict[str, object] = {sym: None for sym in symbols}

    # Shared rate-limit window (portfolio-level).
    order_timestamps: deque[datetime] = deque()

    # Merge all bars into one timeline sorted by (ts_open, symbol).
    # Sorting by symbol within the same timestamp ensures deterministic ordering
    # so that the cross-sectional ranking is reproducible across runs.
    timeline = sorted(
        ((b.ts_open, sym, b) for sym, bars in all_bars.items() for b in bars),
        key=lambda t: (t[0], t[1]),
    )

    for ts, sym, bar in timeline:
        broker = brokers[sym]
        bar_idx = bar_counts[sym]
        bar_counts[sym] += 1
        last_bars[sym] = bar

        # 1. Settle fills from previous bar's signals.
        pos_before = broker.positions().get(sym)
        new_fills = broker.settle_fills(bar)
        pos_after = broker.positions().get(sym)
        _update_trades(sym, pos_before, pos_after, new_fills, bar_idx, open_entries, closed_trades)

        # 2. Check stop-losses.
        pos_before_stop = broker.positions().get(sym)
        stop_fills = broker.check_stops(bar)
        pos_after_stop = broker.positions().get(sym)
        _update_trades(sym, pos_before_stop, pos_after_stop, stop_fills, bar_idx, open_entries, closed_trades)

        # 3. Equity snapshot at bar close.
        equity = broker.portfolio_value({sym: bar.close})
        equity_curves[sym].append(equity)

        # Track day boundary for daily PnL.
        bar_date = ts.date()
        if current_day[sym] is None or bar_date != current_day[sym]:
            day_start_equity[sym] = equity
            current_day[sym] = bar_date

        # 4. Update history.
        histories[sym].append(bar)

        pos_now = broker.positions().get(sym)
        if pos_now and pos_now.qty > Decimal("0"):
            bars_in_position[sym] += 1

        # 5. Build strategy context and get intents.
        #    The shared strategy instance updates _roc_cache for sym and returns
        #    Intents based on cross-sectional rank across all cached symbols.
        current_position = pos_now or Position(
            symbol=sym, qty=Decimal("0"), avg_price=Decimal("0")
        )
        ctx = StrategyContext(
            symbol=sym,
            history=list(histories[sym]),
            position=current_position,
            equity=equity,
            params=sc.params,
        )
        intents = strategy.on_bar(ctx)

        # 6. Build portfolio-level open positions for MaxOpenPositions rule.
        #    Aggregate across all per-symbol brokers.
        all_open: dict[str, Decimal] = {}
        for s in symbols:
            p = brokers[s].positions().get(s)
            if p and p.qty > Decimal("0"):
                all_open[s] = p.qty

        daily_pnl = equity - day_start_equity[sym]
        risk_state = build_risk_state(
            equity=equity,
            cash=broker.equity(),
            daily_pnl=daily_pnl,
            order_timestamps=order_timestamps,
            bar_ts=ts,
            open_positions=all_open,
            mark_prices={sym: float(bar.close)},
        )

        # 7. Submit approved orders.
        for intent in intents:
            decision = risk.evaluate(intent, risk_state)
            if not decision.verdict.allowed:
                continue

            order = Order(
                order_id=new_order_id(),
                run_id=run_ids[sym],
                strategy_id=intent.strategy_id,
                symbol=intent.symbol,
                side=intent.side,
                qty=intent.qty,
                order_type=OrderType.MARKET,
                limit_price=None,
                ts_submitted=ts,
                status=OrderStatus.NEW,
            )
            accepted = broker.submit(order)
            submitted_orders[sym].append(accepted)
            order_timestamps.append(ts)

            if intent.side == Side.BUY and intent.stop_price is not None:
                broker.register_stop(sym, intent.stop_price)
            elif intent.side == Side.SELL:
                broker.clear_stop(sym)

    # 8. Final settlement: fill any orders queued on the last bar of each symbol.
    for sym in symbols:
        lb = last_bars[sym]
        if lb is None:
            continue
        synthetic = Bar(
            symbol=lb.symbol,
            timeframe=lb.timeframe,
            ts_open=lb.ts_open,
            open=lb.close,
            high=lb.close,
            low=lb.close,
            close=lb.close,
            volume=Decimal("0"),
        )
        broker = brokers[sym]
        pos_before_final = broker.positions().get(sym)
        final_fills = broker.settle_fills(synthetic)
        pos_after_final = broker.positions().get(sym)
        _update_trades(
            sym, pos_before_final, pos_after_final, final_fills,
            bar_counts[sym], open_entries, closed_trades,
        )
        final_equity = broker.portfolio_value({})
        equity_curves[sym].append(final_equity)

    # 9. Wrap per-symbol results into BacktestResult objects.
    symbol_results: list[SymbolResult] = []
    for sym in symbols:
        bars = all_bars[sym]
        timeframe = bars[0].timeframe
        metrics = compute_metrics(
            equity_curve=equity_curves[sym],
            trades=closed_trades[sym],
            timeframe=timeframe,
            bars_in_position=bars_in_position[sym],
        )
        bt_result = BacktestResult(
            run_id=run_ids[sym],
            n_bars=len(bars),
            final_equity=equity_curves[sym][-1],
            metrics=metrics,
            equity_curve=equity_curves[sym],
            orders=submitted_orders[sym],
            fills=brokers[sym].recent_fills(),
        )
        symbol_results.append(
            SymbolResult(symbol=sym, run_id=run_ids[sym], result=bt_result, bars=bars)
        )

    return symbol_results


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

    data_root = Path(data_dir) if data_dir else Path("data")
    out_root = Path(out_dir) if out_dir else None

    log = setup_logging(
        log_dir=settings.env.log_dir,
        level=settings.env.log_level,
        run_id="xsmom-bt",
    )
    log.info(
        "xsmom_backtest_start",
        strategy=sc.name,
        symbols=symbols,
        timeframe=timeframe,
    )

    # Load bars for each symbol.
    all_bars: dict[str, list[Bar]] = {}
    run_ids: dict[str, str] = {}
    for symbol in symbols:
        normalized = symbol.replace("/", "_")
        data_file = data_root / f"{normalized}_{timeframe}.csv"
        if not data_file.exists():
            raise FileNotFoundError(
                f"Data file not found for {symbol}: {data_file}\n"
                f"Fetch:  cryptobot fetch-history --symbol {symbol} "
                f"--timeframe {timeframe} --since 2023-01-01\n"
                f"Export: cryptobot export-history --symbol {symbol} "
                f"--timeframe {timeframe}"
            )
        run_ids[symbol] = new_run_id("bt")
        log.info("loading_bars", symbol=symbol, file=str(data_file))
        bars = load_bars_from_csv(data_file, symbol, timeframe)
        if len(bars) < 2:
            raise ValueError(f"Too few bars for {symbol}: {len(bars)}")
        log.info("bars_loaded", symbol=symbol, n=len(bars))
        all_bars[symbol] = bars

    # Run the cross-sectional portfolio backtest.
    symbol_results = _run_portfolio(settings, all_bars, run_ids)

    # Journal writes.
    init_db(settings.env.db_url)
    db_engine = build_db_engine(settings.env.db_url)
    sf = make_session_factory(db_engine)
    for sr in symbol_results:
        record_run_start(
            sf, sr.run_id, mode="backtest", strategy_name=sc.name,
            notes=f"xsmom symbol={sr.symbol}",
        )
        for order in sr.result.orders:
            record_order(sf, sr.run_id, order)
        for fill in sr.result.fills:
            record_fill(sf, fill)
        record_equity_snapshots_bulk(
            sf, sr.run_id,
            [b.ts_open for b in sr.bars],
            sr.result.equity_curve,
        )
        record_run_end(
            sf, sr.run_id,
            notes=f"final_equity={sr.result.final_equity:.2f}",
        )

    # Portfolio aggregation and reporting.
    pm = compute_portfolio_metrics(symbol_results, timeframe)
    _print_portfolio_report(symbol_results, pm)

    if out_root is not None:
        for sr in symbol_results:
            sym_dir = out_root / sr.symbol.replace("/", "_")
            write_result_bundle(sr.result, sr.bars, sc.name, sym_dir)
        write_portfolio_summary(symbol_results, pm, sc.name, timeframe, out_root)
        log.info("results_written", out_dir=str(out_root))

    log.info(
        "xsmom_backtest_complete",
        portfolio_sharpe=pm.sharpe,
        portfolio_return=round(pm.total_return, 4),
        total_trades=pm.n_trades,
        verdict=pm.verdict,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="XSMOM cross-sectional backtest (shared strategy instance)"
    )
    parser.add_argument("config", help="Path to YAML config")
    parser.add_argument("--data-dir", default="data", help="Directory with OHLCV CSV files")
    parser.add_argument("--out-dir", default=None, help="Output directory for results")
    args = parser.parse_args()
    main(args.config, data_dir=args.data_dir, out_dir=args.out_dir)
