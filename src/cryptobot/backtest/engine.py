"""Bar-by-bar backtest engine.

Execution order within each bar:
  1. settle_fills(bar)  — fill pending orders at this bar's open (no look-ahead)
  2. check_stops(bar)   — close positions whose stop-loss was breached
  3. record any trades caused by steps 1-2 (position-change detection)
  4. equity snapshot    — mark-to-market at bar close
  5. append bar to history
  6. strategy.on_bar()  — generate intents from history ending at this bar
  7. risk checks        — filter intents
  8. submit approved    — queue orders for the NEXT bar's fill

No journal writes, no DB calls.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

from cryptobot.backtest.metrics import ClosedTrade, Metrics, compute_metrics
from cryptobot.core.ids import new_order_id
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
from cryptobot.risk.manager import RiskManager
from cryptobot.risk.state_builder import build_risk_state
from cryptobot.strategy.base import Strategy, StrategyContext


@dataclass
class BacktestResult:
    run_id: str
    n_bars: int
    final_equity: float
    metrics: Metrics
    equity_curve: list[float]   # equity_curve[0] = starting cash, [i+1] = end of bar i
    orders: list[Order]         # orders submitted during the run (excludes synthetic stops)
    fills: list[Fill]           # all fills including stop-loss fills


# Internal: full entry record stored while a trade is open.
class _OpenEntry(NamedTuple):
    price: float
    qty: float
    bar_idx: int
    fee: float


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        broker: BacktestBroker,
        risk: RiskManager,
        bars: list[Bar],
    ) -> None:
        self._strategy = strategy
        self._broker = broker
        self._risk = risk
        self._bars = bars

    def run(self, run_id: str) -> BacktestResult:
        bars = self._bars
        if not bars:
            raise ValueError("bars list is empty — nothing to backtest")

        symbol = bars[0].symbol
        timeframe = bars[0].timeframe
        starting_equity = self._broker.portfolio_value({})

        equity_curve: list[float] = [starting_equity]
        history: list[Bar] = []
        closed_trades: list[ClosedTrade] = []
        submitted_orders: list[Order] = []
        bars_in_position: int = 0

        # Tracks the open entry while a long position is held.
        open_entry: _OpenEntry | None = None

        # For orders_this_minute rate-limit tracking.
        order_timestamps: deque[datetime] = deque()

        # Day-level PnL tracking for MaxDailyLoss rule.
        day_start_equity = starting_equity
        current_day = bars[0].ts_open.date()

        for bar_idx, bar in enumerate(bars):

            # --- 1. Settle fills from previous bar's signals ---
            pos_before = self._broker.positions().get(symbol)
            new_fills = self._broker.settle_fills(bar)
            pos_after = self._broker.positions().get(symbol)

            open_entry = _record_trade(
                symbol, pos_before, pos_after, new_fills,
                bar_idx, open_entry, closed_trades,
            )

            # --- 2. Check stop-losses ---
            pos_before_stop = self._broker.positions().get(symbol)
            stop_fills = self._broker.check_stops(bar)
            pos_after_stop = self._broker.positions().get(symbol)

            open_entry = _record_trade(
                symbol, pos_before_stop, pos_after_stop, stop_fills,
                bar_idx, open_entry, closed_trades,
            )

            # --- 3. Equity snapshot at bar close ---
            equity = self._broker.portfolio_value({symbol: bar.close})
            equity_curve.append(equity)

            # Track day-start equity for daily PnL calculation.
            bar_date = bar.ts_open.date()
            if bar_date != current_day:
                day_start_equity = equity
                current_day = bar_date

            # --- 4. Update history ---
            history.append(bar)

            # Count bars holding a position.
            pos_now = self._broker.positions().get(symbol)
            if pos_now and pos_now.qty > Decimal("0"):
                bars_in_position += 1

            # --- 5. Build strategy context ---
            current_position = pos_now or Position(
                symbol=symbol, qty=Decimal("0"), avg_price=Decimal("0")
            )
            ctx = StrategyContext(
                symbol=symbol,
                history=list(history),
                position=current_position,
                equity=equity,
                params=self._strategy.params,
            )

            # --- 6. Get strategy intents ---
            intents = self._strategy.on_bar(ctx)

            # --- 7. Risk state for this bar ---
            daily_pnl = equity - day_start_equity
            open_positions = {symbol: pos_now.qty} if pos_now else {}
            risk_state = build_risk_state(
                equity=equity,
                cash=self._broker.equity(),  # BacktestBroker.equity() returns cash
                daily_pnl=daily_pnl,
                order_timestamps=order_timestamps,
                bar_ts=bar.ts_open,
                open_positions=open_positions,
                mark_prices={symbol: float(bar.close)},
            )

            # --- 8. Submit approved orders ---
            for intent in intents:
                decision = self._risk.evaluate(intent, risk_state)
                if not decision.verdict.allowed:
                    continue

                order = Order(
                    order_id=new_order_id(),
                    run_id=run_id,
                    strategy_id=intent.strategy_id,
                    symbol=intent.symbol,
                    side=intent.side,
                    qty=intent.qty,
                    order_type=OrderType.MARKET,
                    limit_price=None,
                    ts_submitted=bar.ts_open,
                    status=OrderStatus.NEW,
                )
                accepted = self._broker.submit(order)
                submitted_orders.append(accepted)
                order_timestamps.append(bar.ts_open)

                if intent.side == Side.BUY and intent.stop_price is not None:
                    self._broker.register_stop(intent.symbol, intent.stop_price)
                elif intent.side == Side.SELL:
                    self._broker.clear_stop(intent.symbol)

        # Settle any orders queued on the final bar (fill at final bar's close).
        last_bar = bars[-1]
        synthetic_next = Bar(
            symbol=last_bar.symbol,
            timeframe=last_bar.timeframe,
            ts_open=last_bar.ts_open,
            open=last_bar.close,
            high=last_bar.close,
            low=last_bar.close,
            close=last_bar.close,
            volume=Decimal("0"),
        )
        pos_before_final = self._broker.positions().get(symbol)
        final_fills = self._broker.settle_fills(synthetic_next)
        pos_after_final = self._broker.positions().get(symbol)
        open_entry = _record_trade(
            symbol, pos_before_final, pos_after_final, final_fills,
            len(bars), open_entry, closed_trades,
        )

        final_equity = self._broker.portfolio_value({})
        equity_curve.append(final_equity)

        metrics = compute_metrics(
            equity_curve=equity_curve,
            trades=closed_trades,
            timeframe=timeframe,
            bars_in_position=bars_in_position,
        )

        return BacktestResult(
            run_id=run_id,
            n_bars=len(bars),
            final_equity=final_equity,
            metrics=metrics,
            equity_curve=equity_curve,
            orders=submitted_orders,
            fills=self._broker.recent_fills(),
        )


def _record_trade(
    symbol: str,
    pos_before: Position | None,
    pos_after: Position | None,
    fills: list[Fill],
    bar_idx: int,
    open_entry: _OpenEntry | None,
    closed_trades: list[ClosedTrade],
) -> _OpenEntry | None:
    """Detect position open/close from a before/after position snapshot.

    Returns the updated open_entry (may be set, cleared, or unchanged).
    """
    before_qty = pos_before.qty if pos_before else Decimal("0")
    after_qty = pos_after.qty if pos_after else Decimal("0")

    if not fills:
        return open_entry

    if before_qty == Decimal("0") and after_qty > Decimal("0"):
        # Position opened.
        f = fills[0]
        return _OpenEntry(
            price=float(f.price),
            qty=float(f.qty),
            bar_idx=bar_idx,
            fee=float(f.fee),
        )

    if before_qty > Decimal("0") and after_qty == Decimal("0"):
        # Position closed.
        if open_entry is not None:
            f = fills[-1]
            pnl = (
                (float(f.price) - open_entry.price) * open_entry.qty
                - open_entry.fee
                - float(f.fee)
            )
            closed_trades.append(
                ClosedTrade(
                    symbol=symbol,
                    entry_price=open_entry.price,
                    exit_price=float(f.price),
                    qty=open_entry.qty,
                    pnl=pnl,
                    n_bars=bar_idx - open_entry.bar_idx,
                )
            )
        return None

    return open_entry
