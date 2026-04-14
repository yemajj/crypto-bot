"""Fee and slippage models.

A small, testable module — mistakes here flip PnL signs in backtests, so
Phase 3 will ship with unit tests pinning the numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FeeModel:
    """Flat bps fee + size-tiered bps slippage.

    Slippage is tiered by order notional to reflect real-world market impact:
      - Small  (< small_order_threshold):  slippage_bps × 1
      - Medium (< large_order_threshold):  slippage_bps × 2
      - Large  (≥ large_order_threshold):  slippage_bps × 3

    Thresholds are in quote currency (e.g. USDT).
    """

    taker_bps: float = 10.0              # 0.10%
    maker_bps: float = 5.0
    slippage_bps: float = 5.0            # base slippage for small orders
    small_order_threshold: float = 1_000.0   # < $1k → 1× slippage
    large_order_threshold: float = 10_000.0  # ≥ $10k → 3× slippage

    def fee(self, notional: Decimal, *, taker: bool = True) -> Decimal:
        bps = self.taker_bps if taker else self.maker_bps
        return (notional * Decimal(str(bps)) / Decimal("10000")).quantize(Decimal("0.00000001"))

    def apply_slippage(self, price: Decimal, qty: Decimal, *, buy: bool) -> Decimal:
        """Worsen the price by tiered slippage bps based on order notional.

        Parameters
        ----------
        price:
            Raw fill price (bar.open or stop price).
        qty:
            Order quantity in base currency.
        buy:
            True for a buy (price is worsened upward), False for a sell.
        """
        notional = float(price * qty)
        if notional >= self.large_order_threshold:
            multiplier = 3
        elif notional >= self.small_order_threshold:
            multiplier = 2
        else:
            multiplier = 1

        effective_bps = self.slippage_bps * multiplier
        adj = Decimal(str(effective_bps)) / Decimal("10000")
        if buy:
            return (price * (Decimal("1") + adj)).quantize(Decimal("0.00000001"))
        return (price * (Decimal("1") - adj)).quantize(Decimal("0.00000001"))
