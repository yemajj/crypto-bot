"""Integration tests for BacktestEngine.

Uses synthetic bars to verify:
- Fills happen at next-bar open (not signal-bar close).
- Stop-loss fires when bar.low <= stop_price.
- Metrics are computed and non-trivial.
- No look-ahead bias: strategy sees only bars up to and including current bar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cryptobot.backtest.engine import BacktestEngine
from cryptobot.backtest.metrics import ClosedTrade, compute_metrics
from cryptobot.core.types import Bar, Intent, OrderType, Position, Side
from cryptobot.execution.backtest_broker import BacktestBroker
from cryptobot.execution.fees import FeeModel
from cryptobot.risk.manager import RiskManager
from cryptobot.risk.rules import KillSwitchFile, MaxDailyLoss, RequireStopLoss
from cryptobot.strategy.base import Strategy, StrategyContext
from cryptobot.strategy.sma_crossover import SmaCrossover

_SYMBOL = "BTC/USDT"
_TF = "1h"
_TS0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(
    close: float,
    idx: int,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    c = Decimal(str(close))
    o = Decimal(str(open_)) if open_ is not None else c
    h = Decimal(str(high)) if high is not None else c * Decimal("1.005")
    l = Decimal(str(low)) if low is not None else c * Decimal("0.995")
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        ts_open=_TS0 + timedelta(hours=idx),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("10"),
    )


def _zero_fee_model() -> FeeModel:
    return FeeModel(taker_bps=0.0, maker_bps=0.0, slippage_bps=0.0)


def _real_fee_model() -> FeeModel:
    return FeeModel(taker_bps=10.0, maker_bps=5.0, slippage_bps=5.0)


def _minimal_risk() -> RiskManager:
    """No rules that would block normal operation in tests."""
    return RiskManager([])


# ---------------------------------------------------------------------------
# Stub strategy: emits a single BUY on bar 60, SELL on bar 80.
# ---------------------------------------------------------------------------

class _FixedSignalStrategy(Strategy):
    """Emits exactly one BUY and one SELL at configurable bar indices."""

    name = "fixed_signal"

    def __init__(self, buy_bar: int, sell_bar: int) -> None:
        super().__init__()
        self._buy_bar = buy_bar
        self._sell_bar = sell_bar
        self._bar_count = 0

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        self._bar_count += 1
        idx = self._bar_count - 1

        if idx == self._buy_bar and ctx.position.qty == Decimal("0"):
            return [
                Intent(
                    strategy_id=self.name,
                    symbol=ctx.symbol,
                    side=Side.BUY,
                    qty=Decimal("0.1"),
                    order_type=OrderType.MARKET,
                    stop_price=Decimal("1.00"),  # very low — won't be hit
                    reason="test_buy",
                )
            ]
        if idx == self._sell_bar and ctx.position.qty > Decimal("0"):
            return [
                Intent(
                    strategy_id=self.name,
                    symbol=ctx.symbol,
                    side=Side.SELL,
                    qty=ctx.position.qty,
                    order_type=OrderType.MARKET,
                    reason="test_sell",
                )
            ]
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fill_happens_at_next_bar_open():
    """BUY signal on bar N must fill at bar N+1's open, not bar N's close."""
    # Bar 5 close = 100, bar 6 open = 110. Fill price must be ~110 (±slippage).
    n = 10
    closes = [100.0] * n
    opens = [100.0] * n
    opens[6] = 110.0   # fill should be here, not at bar 5's close of 100

    bars = [
        _bar(closes[i], i, open_=opens[i])
        for i in range(n)
    ]

    strategy = _FixedSignalStrategy(buy_bar=5, sell_bar=9)
    broker = BacktestBroker(starting_cash=10_000.0, fees=_zero_fee_model())
    engine = BacktestEngine(strategy, broker, _minimal_risk(), bars)
    result = engine.run("test")

    fills = broker.recent_fills()
    assert len(fills) >= 1
    # First fill is the BUY — should be at bar 6's open (110), not 100.
    buy_fill = fills[0]
    assert float(buy_fill.price) == pytest.approx(110.0, rel=1e-4)


def test_fees_reduce_cash():
    """Fees must be non-zero when FeeModel has non-zero bps."""
    n = 10
    bars = [_bar(100.0 + i, i) for i in range(n)]
    strategy = _FixedSignalStrategy(buy_bar=2, sell_bar=7)
    broker_no_fee = BacktestBroker(starting_cash=10_000.0, fees=_zero_fee_model())
    broker_fee = BacktestBroker(starting_cash=10_000.0, fees=_real_fee_model())

    engine_no_fee = BacktestEngine(strategy, broker_no_fee, _minimal_risk(), bars)
    strategy2 = _FixedSignalStrategy(buy_bar=2, sell_bar=7)
    engine_fee = BacktestEngine(strategy2, broker_fee, _minimal_risk(), bars)

    r_no_fee = engine_no_fee.run("a")
    r_fee = engine_fee.run("b")

    assert r_fee.final_equity < r_no_fee.final_equity


def test_stop_loss_triggers_correctly():
    """If bar.low dips below stop_price, position should be closed."""
    bars = [
        _bar(100.0, 0),
        _bar(100.0, 1),
        _bar(100.0, 2),
        _bar(100.0, 3, low=10.0),   # bar 3: low = 10 → triggers stop
        _bar(100.0, 4),
        _bar(100.0, 5),
    ]

    class _BuyThenHold(Strategy):
        name = "buy_then_hold"
        def on_bar(self, ctx: StrategyContext) -> list[Intent]:
            if len(ctx.history) == 2 and ctx.position.qty == Decimal("0"):
                return [
                    Intent(
                        strategy_id=self.name,
                        symbol=ctx.symbol,
                        side=Side.BUY,
                        qty=Decimal("0.1"),
                        order_type=OrderType.MARKET,
                        stop_price=Decimal("50.0"),   # stop at 50; bar 3 low=10 hits it
                        reason="buy",
                    )
                ]
            return []

    strategy = _BuyThenHold()
    broker = BacktestBroker(starting_cash=10_000.0, fees=_zero_fee_model())
    engine = BacktestEngine(strategy, _minimal_risk(), _minimal_risk(), bars)

    # Rebuild with correct args.
    broker = BacktestBroker(starting_cash=10_000.0, fees=_zero_fee_model())
    engine = BacktestEngine(_BuyThenHold(), broker, _minimal_risk(), bars)
    engine.run("stop_test")

    # Position should be flat after the stop.
    pos = broker.positions().get(_SYMBOL)
    assert pos is None or pos.qty == Decimal("0")


def test_result_has_metrics_populated():
    """Engine result must include a Metrics object with non-default values.

    Bars designed to force one full round-trip trade:
    - Phase 1 (10 bars): fast stays below slow (flat low prices)
    - Phase 2 (1 bar):   sharp spike forces fast above slow → BUY signal
    - Phase 3 (10 bars): high plateau (held in position)
    - Phase 4 (1 bar):   sharp drop forces fast below slow → SELL signal
    - Phase 5 (5 bars):  after exit
    """
    fast, slow = 3, 5
    closes = (
        [50.0] * 10          # flat low — fast ≈ slow ≈ 50, no crossover yet
        + [200.0]             # spike: fast(3) ≈ 167 > slow(5) ≈ 110 → bullish crossover
        + [200.0] * 10        # hold high
        + [50.0]              # drop: fast(3) ≈ 150 < slow(5) ≈ 170 → bearish crossover
        + [50.0] * 5          # after exit
    )
    bars = [_bar(c, i) for i, c in enumerate(closes)]

    strat = SmaCrossover(params={
        "fast": fast,
        "slow": slow,
        "atr_window": 3,
        "risk_per_trade_pct": 0.01,
    })
    broker = BacktestBroker(starting_cash=10_000.0, fees=_real_fee_model())
    engine = BacktestEngine(strat, broker, _minimal_risk(), bars)
    result = engine.run("metrics_test")

    assert result.n_bars == len(bars)
    assert len(result.equity_curve) >= len(bars)
    assert result.metrics.n_trades >= 1
    # Must have tracked equity; not just starting cash repeated.
    assert len(set(result.equity_curve)) > 1


def test_no_lookahead_bias():
    """Strategy must not receive bars beyond the current bar index."""

    class _InspectHistoryLength(Strategy):
        """Records the length of history at each bar call."""
        name = "inspect"
        def __init__(self) -> None:
            super().__init__()
            self.lengths: list[int] = []
        def on_bar(self, ctx: StrategyContext) -> list[Intent]:
            self.lengths.append(len(ctx.history))
            return []

    n = 10
    bars = [_bar(100.0, i) for i in range(n)]
    strat = _InspectHistoryLength()
    broker = BacktestBroker(starting_cash=10_000.0, fees=_zero_fee_model())
    engine = BacktestEngine(strat, broker, _minimal_risk(), bars)
    engine.run("bias_test")

    # At bar i (0-indexed), history length must be i+1.
    assert strat.lengths == list(range(1, n + 1))


def test_metrics_computation():
    """compute_metrics returns sensible values for known equity curves."""
    # Flat equity → no return, no drawdown.
    flat = [10_000.0] * 100
    m = compute_metrics(flat, [], "1h")
    assert m.total_return == pytest.approx(0.0)
    assert m.max_drawdown == pytest.approx(0.0)
    assert m.n_trades == 0

    # Monotone growth → positive return, zero drawdown.
    growing = [10_000.0 + i * 10 for i in range(100)]
    m2 = compute_metrics(growing, [], "1h")
    assert m2.total_return > 0
    assert m2.max_drawdown == pytest.approx(0.0)

    # Known drawdown: peak at 200, trough at 100 → 50% drawdown.
    dd_curve = [100.0, 200.0, 100.0]
    m3 = compute_metrics(dd_curve, [], "1d")
    assert m3.max_drawdown == pytest.approx(0.50)

    # Single winning trade.
    trades = [ClosedTrade("BTC/USDT", 100.0, 110.0, 1.0, pnl=10.0, n_bars=5)]
    m4 = compute_metrics([10_000.0, 10_010.0], trades, "1h")
    assert m4.n_trades == 1
    assert m4.hit_rate == pytest.approx(1.0)
    assert m4.profit_factor == pytest.approx(0.0)  # no losses → 0 by convention
