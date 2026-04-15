"""Opening Range Breakout (ORB) strategy.

The "session" is defined as a UTC calendar day (00:00 – 23:59 UTC).  This is
a reproducible, exchange-neutral boundary that works for 24/7 crypto markets.

Scoring logic:
  - During the opening range (first ``orb_bars`` bars of the session): 0.0
  - After the range is established (and before the entry cutoff):
      close > orb_high  →  +1.0  (bullish breakout)
      close < orb_low   →  -1.0  (bearish breakdown)
      otherwise         →   0.0  (inside range, no signal)
  - After ``max_entry_bar`` bars have passed in the session: 0.0 (no new entries)

This strategy inherits ``ScoringStrategy.on_bar()``, which converts the score
to a BUY/SELL Intent with ATR-based stop-loss sizing.

One Freqtrade-inspired addition: an optional ``volume_confirm`` flag that
requires the breakout bar's volume to exceed the rolling average before
emitting a non-zero score.  Default is ``False`` so baseline validation is
uncontaminated by extra parameters.
"""

from __future__ import annotations

from datetime import date

from cryptobot.core.types import Bar
from cryptobot.strategy.base import ScoringStrategy, StrategyContext
from cryptobot.strategy.registry import register_strategy


def _session_bars_before(history: list[Bar], current: Bar) -> list[Bar]:
    """Return bars from the same UTC calendar day that precede *current*."""
    session: date = current.ts_open.date()
    return [b for b in history if b.ts_open.date() == session and b.ts_open < current.ts_open]


@register_strategy("orb")
class ORBStrategy(ScoringStrategy):
    """Opening Range Breakout strategy.

    Params (all optional):
        orb_bars                  int   default 2     — bars forming the opening range
        max_entry_bar             int   default 16    — no new entries after this many bars
                                                        have elapsed in the session
        volume_confirm            bool  default False — require above-average volume on the
                                                        breakout bar before signalling
        volume_window             int   default 20    — lookback window for average volume
        atr_window                int   default 14    — passed through to on_bar()
        risk_per_trade_pct        float default 0.005 — passed through to on_bar()
        stop_distance_multiplier  float default 1.5   — passed through to on_bar()
        max_position_notional_pct float default 0.10  — passed through to on_bar()
        buy_threshold             float default 0.5   — fires on score=+1.0
        sell_threshold            float default -0.5  — fires on score=-1.0
    """

    bucket = "breakout"

    def signal_score(self, ctx: StrategyContext) -> float:
        bars = ctx.history
        if len(bars) < 2:
            return 0.0

        orb_bars_count = int(ctx.params.get("orb_bars", 2))
        max_entry_bar = int(ctx.params.get("max_entry_bar", 16))

        current = bars[-1]
        session_bars = _session_bars_before(bars, current)

        # Opening range not yet complete — still accumulating range bars.
        if len(session_bars) < orb_bars_count:
            return 0.0

        # Entry cutoff: too late in the session to open a new trade.
        # bar_position is the number of session bars that have elapsed before
        # the current bar (0-indexed: orb_bars_count means the first actionable bar).
        bar_position = len(session_bars)
        if bar_position > max_entry_bar:
            return 0.0

        # Opening range is the first orb_bars_count bars of the session.
        opening = session_bars[:orb_bars_count]
        orb_high = max(float(b.high) for b in opening)
        orb_low = min(float(b.low) for b in opening)

        # Optional Freqtrade-style volume confirmation: breakout bar must have
        # above-average volume.  Disabled by default to keep the baseline clean.
        volume_confirm = bool(ctx.params.get("volume_confirm", False))
        if volume_confirm:
            volume_window = int(ctx.params.get("volume_window", 20))
            if len(bars) >= volume_window + 1:
                prior_vols = [float(b.volume) for b in bars[-(volume_window + 1) : -1]]
                avg_vol = sum(prior_vols) / len(prior_vols)
                if avg_vol > 0 and float(current.volume) <= avg_vol:
                    return 0.0

        current_close = float(current.close)
        if current_close > orb_high:
            return 1.0
        if current_close < orb_low:
            return -1.0
        return 0.0
