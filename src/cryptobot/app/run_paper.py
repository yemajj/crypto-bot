"""Entry point for paper-trading runs.

Paper trading simulates order execution against real market data fetched via
CCXT. No real orders are ever placed. Every decision and fill is journalled
to SQLite and logged as structured JSON.

Usage:
    cryptobot paper --config config/paper.yaml
    # OR
    from cryptobot.app.run_paper import main
    main("config/paper.yaml")

Stop cleanly with Ctrl-C or by creating the kill-switch file (default: ./KILL_SWITCH).

Assumptions and design notes
─────────────────────────────
- Market orders fill at the bar's close price ± slippage, immediately when
  submitted. In a live system a market order fills within seconds of bar close;
  this approximation is reasonable for a bar-based paper-trading engine.
- Limit orders fill on the next bar that touches the limit price.
- Warm-up bars seed the strategy's history but do NOT produce journal entries
  or orders. The run loop counts bars until `warmup_bars` have been consumed.
- Consecutive-loss tracking: after each SELL fill, PnL is compared to the
  recorded entry avg_price. Losses increment `consecutive_losses`; a win resets
  it to 0. After `cooldown_bars` bars the counter is also reset by timeout.
- Stop-loss monitoring: after settle_pending(), check_stops() fires any stop
  registered at BUY time when bar.low <= stop_price. The position is closed at
  stop_price with adverse slippage. Manual SELL also clears the registered stop.
- Gross exposure = equity - cash (mark-to-market position value). This avoids
  accessing private broker state and is algebraically correct.
"""

from __future__ import annotations

import signal
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from cryptobot.config import load_settings
from cryptobot.core.ids import new_order_id, new_run_id
from cryptobot.core.types import (
    Order,
    OrderStatus,
    OrderType,
    Position,
    Signal,
    Side,
)
from cryptobot.core.types import BarGapError
from cryptobot.data.feed import MarketDataFeed
from cryptobot.data.storage import BarStore
from cryptobot.exchanges.ccxt_client import CcxtClient
from cryptobot.execution.fees import FeeModel
from cryptobot.execution.paper_broker import PaperBroker
from cryptobot.journal.writer import (
    build_engine,
    init_db,
    make_session_factory,
    record_equity_snapshot,
    record_fill,
    record_order,
    record_run_end,
    record_run_start,
    record_signal,
)
from cryptobot.monitoring.notify import Notifier
from cryptobot.monitoring.logging_setup import setup_logging
from cryptobot.risk.manager import RiskManager
from cryptobot.risk.rules import (
    CooldownAfterLoss,
    KillSwitchFile,
    MaxDailyLoss,
    MaxGrossExposurePct,
    MaxOpenPositions,
    MaxOrdersPerMinute,
    MaxPositionSizePct,
    OneOrderPerSymbolInFlight,
    RequireStopLoss,
    RiskState,
    SymbolAllowList,
)
from cryptobot.strategy.base import StrategyContext
from cryptobot.strategy.registry import get_strategy


def _handle_sigterm(signum: int, frame: object) -> None:
    """Translate SIGTERM to KeyboardInterrupt for graceful shutdown."""
    raise KeyboardInterrupt


def main(config_path: str | Path) -> str:
    signal.signal(signal.SIGTERM, _handle_sigterm)

    settings = load_settings(config_path)
    run_id = new_run_id("paper")
    log = setup_logging(
        log_dir=settings.env.log_dir,
        level=settings.env.log_level,
        run_id=run_id,
    )

    log.info(
        "paper_start",
        run_id=run_id,
        config=str(config_path),
        strategy=settings.run.strategy.name,
        symbols=settings.run.market.symbols,
        starting_cash=settings.run.starting_cash,
    )

    # --- Initialise journal --------------------------------------------------
    init_db(settings.env.db_url)
    engine = build_engine(settings.env.db_url)
    session_factory = make_session_factory(engine)
    record_run_start(
        session_factory,
        run_id=run_id,
        mode="paper",
        strategy_name=settings.run.strategy.name,
        notes=f"starting_cash={settings.run.starting_cash:.2f}",
    )

    notifier = Notifier(
        bot_token=settings.env.telegram_bot_token,
        chat_id=settings.env.telegram_chat_id,
    )
    notifier.send(
        f"<b>Paper run started</b>\n"
        f"run_id: <code>{run_id}</code>\n"
        f"strategy: {settings.run.strategy.name}\n"
        f"symbols: {', '.join(settings.run.market.symbols)}\n"
        f"cash: ${settings.run.starting_cash:,.2f}"
    )

    # --- Build components ----------------------------------------------------
    rc = settings.run.risk
    fc = settings.run.fees
    sc = settings.run.strategy
    mc = settings.run.market

    fees = FeeModel(
        taker_bps=fc.taker_bps,
        maker_bps=fc.maker_bps,
        slippage_bps=fc.slippage_bps,
    )
    broker = PaperBroker(starting_cash=settings.run.starting_cash, fees=fees)

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
        MaxOpenPositions(rc.max_open_positions),
    ]
    if rc.cooldown_after_losses > 0:
        rules.append(CooldownAfterLoss(rc.cooldown_after_losses))

    risk = RiskManager(rules)

    strategy_cls = get_strategy(sc.name)
    strategy = strategy_cls(params=sc.params)

    client = CcxtClient(
        name=settings.env.exchange_name,
        api_key=settings.env.exchange_api_key,
        api_secret=settings.env.exchange_api_secret,
        testnet=settings.env.exchange_testnet,
    )
    bar_store = BarStore(settings.env.data_dir)
    feed = MarketDataFeed(
        client,
        mc.symbols,
        mc.timeframe,
        warmup_bars=settings.run.warmup_bars,
        poll_interval_seconds=settings.run.poll_interval_seconds,
        bar_store=bar_store,
    )

    # --- Per-run loop state --------------------------------------------------
    history: dict[str, list] = {s: [] for s in mc.symbols}
    day_start_equity: float = broker.equity()
    current_day = datetime.now(timezone.utc).date()
    consecutive_losses: dict[str, int] = {s: 0 for s in mc.symbols}
    cooldown_remaining: dict[str, int] = {s: 0 for s in mc.symbols}
    entry_avg_prices: dict[str, Decimal] = {}
    order_timestamps: deque[datetime] = deque()
    warmup_remaining: int = settings.run.warmup_bars

    # --- Main loop -----------------------------------------------------------
    try:
        for bar in feed.stream():
            symbol = bar.symbol

            # Kill switch check — independent of risk rules.
            if settings.env.kill_switch_file.exists():
                log.warning(
                    "kill_switch_triggered",
                    path=str(settings.env.kill_switch_file),
                )
                break

            # 1. Settle pending limit orders against this bar.
            limit_fills = broker.settle_pending(bar)
            for fill in limit_fills:
                if warmup_remaining <= 0:
                    record_fill(session_factory, fill)
                _process_sell_fill(
                    fill, symbol, entry_avg_prices,
                    consecutive_losses, cooldown_remaining,
                    rc.cooldown_bars, log,
                )

            # 1b. Check stop-loss triggers against this bar's low.
            stop_fills = broker.check_stops(bar)
            for fill in stop_fills:
                if warmup_remaining <= 0:
                    record_fill(session_factory, fill)
                    notifier.send(
                        f"<b>Stop-loss triggered</b> {symbol}\n"
                        f"exit price: {float(fill.price):,.4f}  qty: {float(fill.qty):.6f}\n"
                        f"fee: {float(fill.fee):.4f}  equity: ${broker.equity():,.2f}"
                    )
                _process_sell_fill(
                    fill, symbol, entry_avg_prices,
                    consecutive_losses, cooldown_remaining,
                    rc.cooldown_bars, log,
                )

            # 2. Update mark price for equity().
            broker.update_price(symbol, bar.close)

            # 3. Daily equity reset.
            bar_date = bar.ts_open.date()
            if bar_date != current_day:
                day_start_equity = broker.equity()
                current_day = bar_date

            # 4. Cooldown countdown.
            if cooldown_remaining[symbol] > 0:
                cooldown_remaining[symbol] -= 1
                if cooldown_remaining[symbol] == 0:
                    log.info("cooldown_expired", symbol=symbol)
                    consecutive_losses[symbol] = 0

            # 5. Extend history.
            history[symbol].append(bar)

            equity = broker.equity()
            log.info(
                "bar_close",
                symbol=symbol,
                ts=bar.ts_open.isoformat(),
                close=float(bar.close),
                equity=round(equity, 2),
                cash=round(broker.cash, 2),
                warmup=warmup_remaining > 0,
            )

            if warmup_remaining <= 0:
                record_equity_snapshot(
                    session_factory, run_id, bar.ts_open, equity, broker.cash
                )

            # Skip order submission during warm-up.
            if warmup_remaining > 0:
                warmup_remaining -= 1
                continue

            # 6. Strategy context and intents.
            pos = broker.positions().get(symbol) or Position(
                symbol=symbol, qty=Decimal("0"), avg_price=Decimal("0")
            )
            ctx = StrategyContext(
                symbol=symbol,
                history=list(history[symbol]),
                position=pos,
                equity=equity,
                params=strategy.params,
            )
            intents = strategy.on_bar(ctx)

            # 7. Build RiskState.
            cutoff = bar.ts_open - timedelta(seconds=60)
            while order_timestamps and order_timestamps[0] < cutoff:
                order_timestamps.popleft()

            open_by_symbol: dict[str, int] = {
                s: 1
                for s, p in broker.positions().items()
                if p.qty > Decimal("0")
            }
            risk_state = RiskState(
                equity=equity,
                gross_exposure=equity - broker.cash,
                daily_pnl=equity - day_start_equity,
                orders_this_minute=len(order_timestamps),
                open_intents_by_symbol=open_by_symbol,
                consecutive_losses=consecutive_losses[symbol],
                mark_price_by_symbol={
                    k: float(v) for k, v in broker._mark_prices.items()
                },
            )

            # 8. Risk-check and submit.
            for intent in intents:
                decision = risk.evaluate(intent, risk_state)
                if not decision.verdict.allowed:
                    log.info(
                        "intent_denied",
                        symbol=symbol,
                        side=intent.side.value,
                        reason=decision.verdict.reason,
                    )
                    if "daily" in decision.verdict.reason.lower():
                        notifier.send(
                            f"<b>Daily loss cap hit</b> {symbol}\n"
                            f"daily PnL: ${risk_state.daily_pnl:,.2f}  equity: ${equity:,.2f}\n"
                            f"reason: {decision.verdict.reason}"
                        )
                    continue

                # Journal signal.
                sig = Signal(
                    strategy_id=intent.strategy_id,
                    symbol=intent.symbol,
                    ts=bar.ts_open,
                    strength=1.0 if intent.side == Side.BUY else -1.0,
                    reason=intent.reason,
                )
                record_signal(session_factory, run_id, sig)
                log.info(
                    "signal",
                    symbol=symbol,
                    side=intent.side.value,
                    reason=intent.reason,
                )

                # Snapshot position before fill for PnL calculation on close.
                pos_before_fill = broker.positions().get(symbol)

                order = Order(
                    order_id=new_order_id(),
                    run_id=run_id,
                    strategy_id=intent.strategy_id,
                    symbol=intent.symbol,
                    side=intent.side,
                    qty=intent.qty,
                    order_type=intent.order_type,
                    limit_price=intent.limit_price,
                    ts_submitted=bar.ts_open,
                    status=OrderStatus.NEW,
                )

                submitted = broker.submit(order)
                if submitted.status in {OrderStatus.FILLED, OrderStatus.ACCEPTED}:
                    order_timestamps.append(bar.ts_open)
                record_order(session_factory, run_id, submitted)

                log.info(
                    "order_submitted",
                    symbol=symbol,
                    side=intent.side.value,
                    qty=float(intent.qty),
                    status=submitted.status.value,
                )

                if submitted.status == OrderStatus.FILLED:
                    # Market order filled immediately.
                    fill = next(
                        (f for f in reversed(broker.recent_fills())
                         if f.order_id == order.order_id),
                        None,
                    )
                    if fill is not None:
                        record_fill(session_factory, fill)
                        log.info(
                            "market_fill",
                            symbol=symbol,
                            side=intent.side.value,
                            price=float(fill.price),
                            qty=float(fill.qty),
                            fee=float(fill.fee),
                        )
                        notifier.send(
                            f"<b>Fill</b> {intent.side.value} {symbol}\n"
                            f"price: {float(fill.price):,.4f}  qty: {float(fill.qty):.6f}\n"
                            f"fee: {float(fill.fee):.4f}  equity: ${broker.equity():,.2f}"
                        )
                        if intent.side == Side.SELL and pos_before_fill:
                            _process_sell_fill(
                                fill, symbol, entry_avg_prices,
                                consecutive_losses, cooldown_remaining,
                                rc.cooldown_bars, log,
                                entry_override=pos_before_fill.avg_price,
                            )
                            broker.clear_stop(symbol)
                        elif intent.side == Side.BUY:
                            pos_now = broker.positions().get(symbol)
                            if pos_now and pos_now.qty > Decimal("0"):
                                entry_avg_prices[symbol] = pos_now.avg_price
                                if intent.stop_price is not None:
                                    broker.register_stop(intent.symbol, intent.stop_price)

    except KeyboardInterrupt:
        log.info("paper_shutdown", reason="KeyboardInterrupt")
    except BarGapError as exc:
        log.error("feed_bar_gap_halt", error=str(exc))

    finally:
        final_equity = broker.equity()
        pnl = final_equity - settings.run.starting_cash
        log.info("paper_stopped", run_id=run_id, final_equity=round(final_equity, 2))
        record_run_end(
            session_factory,
            run_id=run_id,
            notes=f"final_equity={final_equity:.2f}",
        )
        notifier.send(
            f"<b>Paper run stopped</b>\n"
            f"run_id: <code>{run_id}</code>\n"
            f"final equity: ${final_equity:,.2f}  PnL: ${pnl:+,.2f}"
        )

    return run_id


# ---------------------------------------------------------------------------
# Helper: update consecutive-loss state after a SELL fill
# ---------------------------------------------------------------------------

def _process_sell_fill(
    fill,
    symbol: str,
    entry_avg_prices: dict[str, Decimal],
    consecutive_losses: dict[str, int],
    cooldown_remaining: dict[str, int],
    cooldown_bars: int,
    log,
    entry_override: Decimal | None = None,
) -> None:
    """Compute trade PnL from a SELL fill and update loss tracking.

    Uses `entry_override` (position avg_price at time of SELL) if provided;
    otherwise falls back to `entry_avg_prices[symbol]` recorded at BUY time.
    If neither is available, PnL tracking is skipped (logged as a warning).
    """
    if fill.qty == Decimal("0"):
        return

    entry_price = entry_override or entry_avg_prices.pop(symbol, None)
    if entry_price is None:
        log.warning("pnl_tracking_no_entry_price", symbol=symbol)
        return

    pnl = (
        (float(fill.price) - float(entry_price)) * float(fill.qty)
        - float(fill.fee)
    )

    if pnl < 0:
        consecutive_losses[symbol] = consecutive_losses.get(symbol, 0) + 1
        if cooldown_bars > 0:
            cooldown_remaining[symbol] = cooldown_bars
        log.info(
            "trade_loss",
            symbol=symbol,
            pnl=round(pnl, 4),
            consecutive_losses=consecutive_losses[symbol],
            cooldown_bars_remaining=cooldown_remaining.get(symbol, 0),
        )
    else:
        consecutive_losses[symbol] = 0
        log.info("trade_win", symbol=symbol, pnl=round(pnl, 4))
