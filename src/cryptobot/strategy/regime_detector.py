"""Market regime detector.

Classifies the current market state into one of three regimes:

  TRENDING       — both the fast and slow SMAs are sloping in the same direction
  RANGING        — default; neither trending nor a volatility spike
  BREAKOUT_WATCH — current ATR > ATR_SPIKE_FACTOR × mean of recent ATR values

Priority order: BREAKOUT_WATCH is checked first, then TRENDING, then RANGING.
Returns RANGING when there are insufficient bars (safe default — no false signals).
"""

from __future__ import annotations

from enum import Enum

from cryptobot.core.types import Bar
from cryptobot.strategy.base import _compute_atr

# Constants
_ATR_WINDOW: int = 14
_ATR_LOOKBACK: int = 20          # bars of ATR history needed for spike detection
_ATR_SPIKE_FACTOR: float = 1.5
_SMA_FAST: int = 20
_SMA_SLOW: int = 50

# Minimum bars needed: max(SMA_SLOW + 1, ATR_WINDOW + ATR_LOOKBACK)
_MIN_BARS: int = max(_SMA_SLOW + 1, _ATR_WINDOW + _ATR_LOOKBACK)


class Regime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    BREAKOUT_WATCH = "breakout_watch"


def _sma(values: list[float], window: int) -> float:
    if len(values) < window:
        return 0.0
    return sum(values[-window:]) / window


def detect_regime(bars: list[Bar]) -> Regime:
    """Classify the current market regime.

    Args:
        bars: Recent bars for a single symbol, oldest-first.

    Returns:
        Regime enum value. Returns RANGING when bars are insufficient.
    """
    if len(bars) < _MIN_BARS:
        return Regime.RANGING

    closes = [float(b.close) for b in bars]

    # --- BREAKOUT_WATCH: ATR spike ---
    # Compute _ATR_LOOKBACK ATR values at positions ending just before the
    # current bar.  Each value uses bars[:end_idx] so the spike bar is excluded
    # from the historical mean.
    atr_series: list[float] = []
    for offset in range(_ATR_LOOKBACK, 0, -1):
        end_idx = len(bars) - offset
        if end_idx >= _ATR_WINDOW + 1:
            atr_val = _compute_atr(bars[:end_idx], _ATR_WINDOW)
            atr_series.append(atr_val)

    current_atr = _compute_atr(bars, _ATR_WINDOW)
    mean_atr = sum(atr_series) / len(atr_series) if atr_series else 0.0

    if mean_atr > 0.0 and current_atr > _ATR_SPIKE_FACTOR * mean_atr:
        return Regime.BREAKOUT_WATCH

    # --- TRENDING: both SMAs slope in the same direction ---
    fast_now = _sma(closes, _SMA_FAST)
    fast_prev = _sma(closes[:-1], _SMA_FAST)
    slow_now = _sma(closes, _SMA_SLOW)
    slow_prev = _sma(closes[:-1], _SMA_SLOW)

    if fast_now != 0.0 and slow_now != 0.0:
        fast_slope = fast_now - fast_prev
        slow_slope = slow_now - slow_prev
        if fast_slope != 0.0 and slow_slope != 0.0:
            if (fast_slope > 0) == (slow_slope > 0):
                return Regime.TRENDING

    return Regime.RANGING
