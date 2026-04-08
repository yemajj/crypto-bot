# Ensemble Strategy System — Design Review Document

> Generated for external review. This document describes a planned extension to the
> cryptobot trading framework. The goal is to add a multi-strategy ensemble on top of the
> existing single-strategy architecture without modifying the run loops, backtest engine,
> or risk layer.

---

## 1. Project Background

**cryptobot** is a safety-first, synchronous crypto trading framework. Data flows:

```
Config → ExchangeClient → MarketDataFeed → Strategy → RiskManager → Broker → Journal
```

Key invariants (must not be broken):
- **Strategies are pure.** `Strategy.on_bar(ctx) -> list[Intent]` — no I/O, no state mutation.
- **The RiskManager is the only path to the broker.** Every Intent must pass `RiskManager.evaluate()`.
- **All brokers share one ABC** (`BacktestBroker`, `PaperBroker`, future `LiveBroker`).
- **Core types use `Decimal` for prices/quantities.** No float in fills/PnL.
- **One synchronous loop.** No asyncio.

---

## 2. Existing Strategy Code (what we are extending)

### `src/cryptobot/strategy/base.py` (current)

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from cryptobot.core.types import Bar, Intent, Position


@dataclass
class StrategyContext:
    symbol: str
    history: list[Bar]        # bars up to and including the current bar
    position: Position        # current net position for this symbol
    equity: float             # portfolio equity in quote currency
    params: dict              # strategy-specific config params from YAML


class Strategy(ABC):
    name: str = "unnamed"

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        """Return zero or more trading intents for this bar. Pure — no side effects."""
        ...
```

### `src/cryptobot/strategy/sma_crossover.py` (current)

```python
from __future__ import annotations

from decimal import Decimal

from cryptobot.core.types import Intent, OrderType, Side
from cryptobot.strategy.base import Strategy, StrategyContext
from cryptobot.strategy.registry import register_strategy


def _sma(values: list[float], window: int) -> float:
    if len(values) < window:
        return 0.0
    return sum(values[-window:]) / window


def _atr(bars, window: int) -> float:
    if len(bars) < window + 1:
        return 0.0
    trs = []
    for i in range(-window, 0):
        b = bars[i]
        prev_close = float(bars[i - 1].close)
        high = float(b.high)
        low = float(b.low)
        close_prev = prev_close
        trs.append(max(high - low, abs(high - close_prev), abs(low - close_prev)))
    return sum(trs) / len(trs)


def _parse_params(params: dict):
    fast = int(params.get("fast", 20))
    slow = int(params.get("slow", 50))
    atr_window = int(params.get("atr_window", 14))
    risk_pct = float(params.get("risk_per_trade_pct", 0.01))
    return fast, slow, atr_window, risk_pct


@register_strategy("sma_crossover")
class SmaCrossover(Strategy):
    name = "sma_crossover"

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        fast, slow, atr_window, risk_pct = _parse_params(ctx.params)
        bars = ctx.history
        closes = [float(b.close) for b in bars]

        fast_now = _sma(closes, fast)
        fast_prev = _sma(closes[:-1], fast)
        slow_now = _sma(closes, slow)
        slow_prev = _sma(closes[:-1], slow)

        if fast_now == 0.0 or slow_now == 0.0:
            return []

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now

        atr = _atr(bars, atr_window)
        if atr == 0.0:
            return []

        current_close = float(bars[-1].close)
        risk_amount = ctx.equity * risk_pct
        stop_distance = atr * 1.5
        qty = Decimal(str(round(risk_amount / stop_distance, 8)))
        stop_price = Decimal(str(round(current_close - stop_distance, 8)))

        if crossed_up and ctx.position.qty <= 0:
            return [Intent(
                strategy_id="sma_crossover",
                symbol=ctx.symbol,
                side=Side.BUY,
                qty=qty,
                order_type=OrderType.MARKET,
                stop_price=stop_price,
                reason=f"SMA crossover up: fast={fast_now:.2f} slow={slow_now:.2f}",
            )]
        if crossed_down and ctx.position.qty > 0:
            return [Intent(
                strategy_id="sma_crossover",
                symbol=ctx.symbol,
                side=Side.SELL,
                qty=ctx.position.qty,
                order_type=OrderType.MARKET,
                reason=f"SMA crossover down: fast={fast_now:.2f} slow={slow_now:.2f}",
            )]
        return []
```

### `src/cryptobot/strategy/registry.py` (current)

```python
from __future__ import annotations

from typing import TypeVar
from cryptobot.strategy.base import Strategy

T = TypeVar("T", bound=type[Strategy])
_REGISTRY: dict[str, type[Strategy]] = {}


def register_strategy(name: str):
    def _decorate(cls: T) -> T:
        if name in _REGISTRY:
            raise ValueError(f"Strategy already registered: {name}")
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return _decorate


def get_strategy(name: str) -> type[Strategy]:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown strategy '{name}'. Registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def registered_names() -> list[str]:
    return sorted(_REGISTRY)
```

### `src/cryptobot/core/types.py` — Key types (current)

```python
@dataclass(frozen=True)
class Bar:
    symbol: str
    timeframe: str
    ts_open: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

@dataclass(frozen=True)
class Intent:
    strategy_id: str
    symbol: str
    side: Side         # Side.BUY or Side.SELL
    qty: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None   # RequireStopLoss rule enforces this on BUY
    reason: str = ""

@dataclass(frozen=True)
class Position:
    symbol: str
    qty: Decimal       # positive = long, negative = short
    avg_price: Decimal # zero when flat
```

---

## 3. The Plan: Ensemble Strategy System

### 3.1 Context

The bot currently has one strategy (`sma_crossover`) wired directly into the run loops via
`strategy.on_bar(ctx) -> list[Intent]`. There is no aggregation, voting, or regime awareness.
The goal is to add three new strategies (RSI, Donchian, Bollinger), a volume-confirmation scorer,
a regime detector, and a weighted ensemble that composes them — while preserving the existing
single-strategy flow with zero changes to the run loops, backtest engine, or risk layer.

---

### 3.2 Architecture

```
Strategy (ABC)                       ← existing, unchanged
  └─ ScoringStrategy (ABC)           ← NEW in base.py
       ├─ SmaCrossover               ← modified: extend ScoringStrategy, add signal_score + ma_type
       ├─ RsiStrategy                ← new
       ├─ DonchianStrategy           ← new
       ├─ BollingerStrategy          ← new
       └─ VolumeSignalStrategy       ← new

EnsembleStrategy(Strategy)           ← new; registered as "ensemble"
  uses RegimeDetector (pure fn)
  uses SignalAggregator (pure class)
```

**Backward compatibility:** The run loops call only `strategy.on_bar(ctx)`. Since both
`ScoringStrategy` and `EnsembleStrategy` implement this interface, no run-loop changes are needed.
`get_strategy("sma_crossover")` still works exactly as before.

---

### 3.3 Files to Create / Modify

| File | Action |
|---|---|
| `src/cryptobot/strategy/base.py` | Add `ScoringStrategy` ABC + `_compute_atr` helper |
| `src/cryptobot/strategy/sma_crossover.py` | Extend `ScoringStrategy`, add `signal_score()`, add `ma_type` param |
| `src/cryptobot/strategy/rsi.py` | New — RSI momentum scorer |
| `src/cryptobot/strategy/donchian.py` | New — Donchian breakout scorer |
| `src/cryptobot/strategy/bollinger.py` | New — Bollinger squeeze scorer |
| `src/cryptobot/strategy/volume_signal.py` | New — Volume confirmation scorer |
| `src/cryptobot/strategy/regime_detector.py` | New — pure `detect_regime(bars) -> Regime` |
| `src/cryptobot/strategy/signal_aggregator.py` | New — `SignalAggregator` with bucketed weights |
| `src/cryptobot/strategy/ensemble.py` | New — `EnsembleStrategy` wrapping the above |
| `config/ensemble.yaml` | New — example runnable config |
| `tests/test_rsi.py` | New |
| `tests/test_donchian.py` | New |
| `tests/test_bollinger.py` | New |
| `tests/test_volume_signal.py` | New |
| `tests/test_regime_detector.py` | New |
| `tests/test_signal_aggregator.py` | New |
| `tests/test_ensemble.py` | New |

**Not modified:** `config/settings.py`, run loops, backtest engine, risk layer, journal.

---

### 3.4 Detailed Implementation

#### Step 1: `strategy/base.py` — Add `ScoringStrategy` + `_compute_atr`

Add `_compute_atr(bars, window) -> float` as a module-level function (same logic as `_atr` in
`sma_crossover.py`, used by `ScoringStrategy.on_bar` and the new modules; `sma_crossover._atr`
is kept intact for its own tests).

Add `ScoringStrategy(Strategy)`:

```python
class ScoringStrategy(Strategy):
    bucket: str = "unknown"   # class attribute: trend|momentum|breakout|volatility|volume

    @abstractmethod
    def signal_score(self, ctx: StrategyContext) -> float:
        """Return score in [-1.0, 1.0]. Pure, no side effects."""
        ...

    def on_bar(self, ctx: StrategyContext) -> list[Intent]:
        """Default: convert signal_score to Intent using threshold params.
        Reads buy_threshold (default 0.30) and sell_threshold (default -0.30)
        from ctx.params. Computes ATR-sized qty and stop_price for BUY.
        """
        ...
```

The default `on_bar` uses `_compute_atr`, reads `atr_window` and `risk_per_trade_pct` from
`ctx.params`, and constructs BUY/SELL Intents with `stop_price` (required by `RequireStopLoss`).

#### Step 2: `strategy/sma_crossover.py` — Extend `ScoringStrategy`

- Change base class to `ScoringStrategy`
- Add `bucket = "trend"` class attribute
- Add `ma_type: str` to `_parse_params` (accepts `"sma"` or `"ema"`, defaults to `"sma"`)
- Add `_ema(values, window) -> float` module-level helper (initialize as SMA on first `window`
  values, then apply multiplier; return `0.0` if `len(values) < window`)
- In `on_bar`, replace `_sma()` calls with `_ma = _ema if ma_type == "ema" else _sma`
- Add `signal_score(ctx) -> float`: same crossover detection as `on_bar` but returns
  `+1.0` / `-1.0` / `0.0`
- **CRITICAL: Keep `_atr` and `_sma` functions unchanged** — `test_sma_crossover.py` imports them
- `_parse_params` now returns 5-tuple: `(fast, slow, atr_window, risk_pct, ma_type)`

#### Step 3: `strategy/rsi.py` — RSI Momentum

```python
@register_strategy("rsi")
class RsiStrategy(ScoringStrategy):
    name = "rsi"
    bucket = "momentum"
```

Module-level `_rsi(closes, window) -> float`:
- Needs `window + 1` values; returns `50.0` (neutral) if insufficient
- Classic Wilder's RSI using equal-weight average for seed:
  `avg_gain = mean(gains[-window:])`, `avg_loss = mean(losses[-window:])`
- Returns `[0.0, 100.0]`

`signal_score` params: `window=14`, `oversold=30.0`, `overbought=70.0`
- RSI ≤ oversold: `return (oversold - rsi) / oversold` → `[0.0, 1.0]`
- RSI ≥ overbought: `return -(rsi - overbought) / (100.0 - overbought)` → `[-1.0, 0.0]`
- Neutral zone: `return 0.0`

#### Step 4: `strategy/donchian.py` — Donchian Breakout

```python
@register_strategy("donchian")
class DonchianStrategy(ScoringStrategy):
    name = "donchian"
    bucket = "breakout"
```

`signal_score` params: `window=20`
- Channel upper = `max(high)` over prior `window` bars (excluding current)
- Channel lower = `min(low)` over prior `window` bars (excluding current)
- `current_close > upper` → `return 1.0`
- `current_close < lower` → `return -1.0`
- Inside channel → `return 0.0`
- Returns `0.0` if insufficient bars (`< window + 1`)

#### Step 5: `strategy/bollinger.py` — Bollinger Squeeze

```python
@register_strategy("bollinger")
class BollingerStrategy(ScoringStrategy):
    name = "bollinger"
    bucket = "volatility"
```

`signal_score` params: `window=20`, `n_std=2.0`
- Compute `(middle, upper, lower, bandwidth)` for current and prior bar's window
- `bandwidth = (upper - lower) / middle`; guard `std == 0 → return 0.0`
- If `bw_now <= bw_prev` (contracting/flat): `return 0.0`
- If expanding: return position of close within bands normalized to `[-1.0, 1.0]`:
  `score = (close - middle) / (upper - middle)`, clamped to `[-1.0, 1.0]`

#### Step 6: `strategy/volume_signal.py` — Volume Confirmation

```python
@register_strategy("volume_signal")
class VolumeSignalStrategy(ScoringStrategy):
    name = "volume_signal"
    bucket = "volume"
```

`signal_score` params: `window=20`
- `avg_vol = mean(volume[-window-1:-1])` (prior window bars, excluding current)
- `vol_ratio = current_vol / avg_vol`; guard `avg_vol <= 0 → return 0.0`
- `vol_ratio <= 1.0`: below-average volume → `return 0.0`
- `vol_factor = min(1.0, (vol_ratio - 1.0) / 2.0)` — 0 at 1×, 1.0 at 3×
- `price_direction = +1 if close > prev_close else -1`
- `return price_direction * vol_factor`

#### Step 7: `strategy/regime_detector.py` — Pure Regime Detection

```python
class Regime(str, Enum):
    TRENDING       = "trending"
    RANGING        = "ranging"
    BREAKOUT_WATCH = "breakout_watch"

def detect_regime(bars: list[Bar]) -> Regime: ...
```

Rules (evaluated in priority order, needs ≥ 52 bars):
1. **BREAKOUT_WATCH**: current 14-bar ATR > 1.5 × mean of last 20 ATR values
2. **TRENDING**: 20-SMA slope direction == 50-SMA slope direction (both up or both down)
3. **RANGING**: default fallback

Returns `RANGING` if fewer than `max(51, ATR_WINDOW + ATR_LOOKBACK)` bars.

Imports `_compute_atr` from `strategy.base`.

#### Step 8: `strategy/signal_aggregator.py` — Weighted Aggregation

```python
DEFAULT_REGIME_WEIGHTS = {
    "trending":       {"trend": 0.35, "momentum": 0.20, "breakout": 0.25, "volatility": 0.10, "volume": 0.10},
    "ranging":        {"trend": 0.10, "momentum": 0.35, "breakout": 0.10, "volatility": 0.30, "volume": 0.15},
    "breakout_watch": {"trend": 0.15, "momentum": 0.10, "breakout": 0.35, "volatility": 0.25, "volume": 0.15},
}

@dataclass(frozen=True)
class AggregationResult:
    final_score: float
    regime: Regime
    agreement_ok: bool
    bucket_scores: dict[str, float]   # for logging

class SignalAggregator:
    def __init__(self, regime_weights=None, buy_threshold=0.30,
                 sell_threshold=-0.30, min_agreeing_buckets=2): ...
    def aggregate(self, bucket_scores: dict[str, float], regime: Regime) -> AggregationResult: ...
```

`aggregate` logic:
1. Get weight table for regime
2. `weighted_sum = sum(score * weight for bucket, weight)` over all 5 buckets (missing → 0.0)
3. `final_score = weighted_sum / total_weight`, clamped `[-1.0, 1.0]`
4. Agreement filter: count buckets with `score != 0.0` and same sign as `final_score`;
   `agreement_ok = count >= min_agreeing_buckets`

#### Step 9: `strategy/ensemble.py` — EnsembleStrategy

```python
@register_strategy("ensemble")
class EnsembleStrategy(Strategy):
    name = "ensemble"
```

Constructor parses `params["strategies"]` (list of `{name, bucket, params}` dicts), instantiates
each sub-strategy, stores as `list[tuple[str, Strategy, dict]]` — (bucket, instance, sub_params).

**Critical:** Each sub-strategy call receives a sub-strategy-specific context:

```python
sub_ctx = StrategyContext(
    symbol=ctx.symbol,
    history=ctx.history,
    position=ctx.position,
    equity=ctx.equity,
    params=sub_params,    # ← sub-strategy's own params, not ensemble's
)
```

`on_bar` flow:
1. For each `(bucket, sub, sub_params)`:
   - If `ScoringStrategy`: call `sub.signal_score(sub_ctx)` → score
   - If plain `Strategy`: call `sub.on_bar(sub_ctx)`, map BUY→+1.0, SELL→-1.0, empty→0.0
   - Multiple strategies in same bucket: average their scores
2. `regime = detect_regime(ctx.history)`
3. `result = self._aggregator.aggregate(bucket_scores, regime)`
4. If not `result.agreement_ok`: return `[]`
5. If `result.final_score >= buy_threshold` and flat: return BUY Intent (ATR-sized, with `stop_price`)
6. If `result.final_score <= sell_threshold` and long: return SELL Intent (full position)
7. Otherwise: return `[]`

Intent `reason` field includes score, regime, and bucket breakdown for observability.

#### Step 10: `config/ensemble.yaml`

```yaml
mode: paper

market:
  symbols: [BTC/USDT]
  timeframe: 1h

strategy:
  name: ensemble
  params:
    strategies:
      - name: sma_crossover
        bucket: trend
        params: {fast: 20, slow: 50, atr_window: 14, risk_per_trade_pct: 0.005}
      - name: rsi
        bucket: momentum
        params: {window: 14, oversold: 30, overbought: 70}
      - name: donchian
        bucket: breakout
        params: {window: 20}
      - name: bollinger
        bucket: volatility
        params: {window: 20, n_std: 2.0}
      - name: volume_signal
        bucket: volume
        params: {window: 20}
    regime_weights:
      trending:       {trend: 0.35, momentum: 0.20, breakout: 0.25, volatility: 0.10, volume: 0.10}
      ranging:        {trend: 0.10, momentum: 0.35, breakout: 0.10, volatility: 0.30, volume: 0.15}
      breakout_watch: {trend: 0.15, momentum: 0.10, breakout: 0.35, volatility: 0.25, volume: 0.15}
    buy_threshold: 0.30
    sell_threshold: -0.30
    min_agreeing_buckets: 2
    atr_window: 14
    risk_per_trade_pct: 0.005

risk:
  max_position_pct: 0.10
  max_gross_exposure_pct: 0.50
  max_daily_loss_pct: 0.02
  max_orders_per_minute: 10
  require_stop_loss: true
  symbol_allow_list: [BTC/USDT]
  max_open_positions: 1
  cooldown_after_losses: 3
  cooldown_bars: 4

fees:
  taker_bps: 10.0
  maker_bps: 5.0
  slippage_bps: 5.0

starting_cash: 10000.0
warmup_bars: 200
poll_interval_seconds: 60.0
```

---

### 3.5 Key Edge Cases to Handle

| Issue | Resolution |
|---|---|
| `test_sma_crossover.py` imports `_atr`, `_sma` from `sma_crossover` | Keep those functions in `sma_crossover.py` unchanged. Add separate `_compute_atr` to `base.py` for new modules. |
| Sub-strategies use `ctx.params` for their own params | Ensemble constructs per-sub `StrategyContext` with `params=sub_params` |
| `RequireStopLoss` risk rule requires stop_price on BUY | Both `ScoringStrategy.on_bar` default AND `EnsembleStrategy.on_bar` always compute and set `stop_price` |
| Multiple sub-strategies in same bucket | Average their scores: `bucket_scores[bucket] = (existing + new) / 2` |
| `std == 0` in Bollinger (flat bars) | Guard: `if std == 0: return 0.0` |
| `avg_vol == 0` in volume scorer | Guard: `if avg_vol <= 0: return 0.0` |
| Regime detector: `ATR_LOOKBACK + ATR_WINDOW` bars needed | Return `RANGING` if insufficient (safe default) |
| `_ema` with insufficient data | Return `0.0` if `len(values) < window` |

---

### 3.6 Test Strategy (per test file)

All tests follow `test_sma_crossover.py` conventions: `_bar(close, idx)`,
`_ctx(history, position, equity, params)` helpers; no mocking.

| File | Key scenarios |
|---|---|
| `test_rsi.py` | too short → 0.0; neutral zone → 0.0; declining bars → oversold → positive score; rising bars → overbought → negative score; standalone BUY intent |
| `test_donchian.py` | too short → 0.0; inside channel → 0.0; breakout above → 1.0; breakdown below → -1.0 |
| `test_bollinger.py` | too short → 0.0; flat bars (std=0) → 0.0; expanding up → positive; contracting → 0.0; score clamped [-1,1] |
| `test_volume_signal.py` | too short → 0.0; below-avg volume → 0.0; 3× avg + up price → positive; 3× avg + down price → negative |
| `test_regime_detector.py` | insufficient bars → RANGING; both SMAs up → TRENDING; ATR spike → BREAKOUT_WATCH; breakout_watch takes priority over trending |
| `test_signal_aggregator.py` | all-positive → agreement_ok=True, score≥0.30; 1 agreeing → agreement_ok=False; correct regime weights applied; missing buckets → pulled toward zero |
| `test_ensemble.py` | registered; ValueError on empty strategies; BUY when all agree + flat; SELL when negative + in position; stop_price on BUY; plain Strategy sub-strategy mapped correctly; sub-strategy params isolation (RSI sees window=14 not ensemble atr_window); sma_crossover standalone regression |

---

### 3.7 Backward Compatibility Guarantees

1. `cryptobot backtest --config config/backtest.yaml` — unchanged (name=sma_crossover)
2. `cryptobot paper --config config/paper.yaml` — unchanged
3. `SmaCrossover` `on_bar` behavior — identical (ma_type defaults to "sma")
4. No run-loop, engine, or risk-layer changes
5. Registry: `get_strategy("sma_crossover")` still works; new names ("rsi", "donchian", etc.) added

---

### 3.8 Verification

```bash
# 1. All existing tests still pass
pytest

# 2. New tests pass
pytest tests/test_rsi.py tests/test_donchian.py tests/test_bollinger.py \
       tests/test_volume_signal.py tests/test_regime_detector.py \
       tests/test_signal_aggregator.py tests/test_ensemble.py -v

# 3. Ensemble backtest (requires OHLCV CSV data)
cryptobot backtest --config config/ensemble.yaml --data data/BTCUSDT_1h.csv

# 4. Confirm sma_crossover still works standalone
cryptobot backtest --config config/backtest.yaml --data data/BTCUSDT_1h.csv

# 5. Verify registry has all names
python -c "from cryptobot.strategy.registry import registered_names; print(registered_names())"
# Expected: ['bollinger', 'donchian', 'ensemble', 'rsi', 'sma_crossover', 'volume_signal']
```

---

## 4. Questions for the Reviewer

These are areas where a second opinion would be most valuable:

1. **Regime detection thresholds** — Is 1.5× ATR for BREAKOUT_WATCH too sensitive or too loose?
   Should the ATR_LOOKBACK period (20 bars) be longer?

2. **Agreement filter** — `min_agreeing_buckets=2` means only 2 of 5 buckets need to agree.
   Is this too permissive? Should it be 3?

3. **Volume scorer as an independent bucket** — Volume is currently a standalone signal that
   contributes its own score. An alternative is to use it only as a multiplier on the other
   scores (i.e., dampen signals on low volume). Which is more principled?

4. **Bollinger squeeze logic** — The score is only non-zero when bandwidth is *expanding*.
   This means the strategy is silent during contractions. Is this the right behavior, or should
   it signal the *anticipation* of a breakout during compression?

5. **EMA seeding** — The `_ema` helper seeds on the first `window` bars using SMA, then applies
   the multiplier. This is the classic approach but means EMA needs extra bars before it's
   "warmed up". Should warmup requirements be surfaced in the config?

6. **Wilder's RSI seeding** — Using equal-weight average for the first `window` gains/losses
   (not a running Wilder smoothing from bar 0). This is a simplification. Is it acceptable for
   a period-14 RSI, or should we implement proper Wilder smoothing from bar 1?

7. **Ensemble stop-loss sizing** — The BUY intent stop distance is `atr * 1.5` (same as
   sma_crossover). Should ensemble use a different multiplier, or allow it to be configured?

8. **Missing bucket behavior** — If a sub-strategy is not configured, its bucket contributes
   `0.0` (neutral). The weighted sum is still divided by total_weight (sum of all 5 bucket
   weights), which means a missing bucket pulls the score toward zero. Is this the right
   treatment, or should the weights be re-normalized to sum to 1.0 across only present buckets?
