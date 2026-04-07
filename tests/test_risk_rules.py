from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cryptobot.core.types import Intent, OrderType, Side
from cryptobot.risk.manager import RiskManager
from cryptobot.risk.rules import (
    KillSwitchFile,
    MaxDailyLoss,
    MaxOrdersPerMinute,
    OneOrderPerSymbolInFlight,
    RequireStopLoss,
    RiskState,
    SymbolAllowList,
)


def _intent(symbol: str = "BTC/USDT", stop: Decimal | None = Decimal("100")) -> Intent:
    return Intent(
        strategy_id="test",
        symbol=symbol,
        side=Side.BUY,
        qty=Decimal("0.01"),
        order_type=OrderType.MARKET,
        stop_price=stop,
        reason="unit test",
    )


def _state(**overrides) -> RiskState:
    base = {
        "equity": 1000.0,
        "gross_exposure": 0.0,
        "daily_pnl": 0.0,
        "orders_this_minute": 0,
        "open_intents_by_symbol": {},
    }
    base.update(overrides)
    return RiskState(**base)  # type: ignore[arg-type]


def test_symbol_allow_list():
    rule = SymbolAllowList(["BTC/USDT"])
    assert rule.check(_intent("BTC/USDT"), _state()).allowed
    assert not rule.check(_intent("ETH/USDT"), _state()).allowed


def test_max_orders_per_minute():
    rule = MaxOrdersPerMinute(3)
    assert rule.check(_intent(), _state(orders_this_minute=2)).allowed
    assert not rule.check(_intent(), _state(orders_this_minute=3)).allowed


def test_one_order_per_symbol_in_flight():
    rule = OneOrderPerSymbolInFlight()
    assert rule.check(_intent(), _state()).allowed
    assert not rule.check(
        _intent(), _state(open_intents_by_symbol={"BTC/USDT": 1})
    ).allowed


def test_require_stop_loss():
    rule = RequireStopLoss()
    assert rule.check(_intent(stop=Decimal("100")), _state()).allowed
    assert not rule.check(_intent(stop=None), _state()).allowed


def test_max_daily_loss():
    rule = MaxDailyLoss(max_loss_pct=0.02)
    assert rule.check(_intent(), _state(daily_pnl=-10.0)).allowed
    assert not rule.check(_intent(), _state(daily_pnl=-25.0)).allowed


def test_kill_switch_file(tmp_path: Path):
    path = tmp_path / "KILL_SWITCH"
    rule = KillSwitchFile(path)
    assert rule.check(_intent(), _state()).allowed
    path.write_text("stop")
    assert not rule.check(_intent(), _state()).allowed


def test_risk_manager_short_circuits_on_first_denial(tmp_path: Path):
    kill = tmp_path / "KILL_SWITCH"
    kill.write_text("stop")
    mgr = RiskManager(
        [
            SymbolAllowList(["BTC/USDT"]),
            KillSwitchFile(kill),     # denies
            RequireStopLoss(),        # would also deny if we got here
        ]
    )
    decision = mgr.evaluate(_intent(stop=None), _state())
    assert not decision.verdict.allowed
    assert "kill switch" in decision.verdict.reason


# Silence the unused-import warning for datetime/timezone if they were removed.
_ = (datetime, timezone)
