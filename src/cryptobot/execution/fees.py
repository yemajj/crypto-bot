"""Fee and slippage models.

A small, testable module — mistakes here flip PnL signs in backtests, so
Phase 3 will ship with unit tests pinning the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FeeModel:
    """Flat bps fee + flat bps slippage.

    Crude but honest for v1. Phase 3/4 may grow a per-venue table.
    """

    taker_bps: float = 10.0   # 0.10%
    maker_bps: float = 5.0
    slippage_bps: float = 5.0

    def fee(self, notional: Decimal, *, taker: bool = True) -> Decimal:
        bps = self.taker_bps if taker else self.maker_bps
        return (notional * Decimal(str(bps)) / Decimal("10000")).quantize(Decimal("0.00000001"))

    def apply_slippage(self, price: Decimal, *, buy: bool) -> Decimal:
        """Worsen the price by `slippage_bps` against the taker."""
        adj = Decimal(str(self.slippage_bps)) / Decimal("10000")
        if buy:
            return (price * (Decimal("1") + adj)).quantize(Decimal("0.00000001"))
        return (price * (Decimal("1") - adj)).quantize(Decimal("0.00000001"))
