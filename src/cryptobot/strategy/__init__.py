"""Strategy engine: base class, registry, and concrete strategies."""

from cryptobot.strategy.base import Strategy, StrategyContext
from cryptobot.strategy.registry import get_strategy, register_strategy

# Import concrete modules to trigger @register_strategy decorators.
# Order matters: leaf strategies before ensemble (which calls get_strategy at init time).
from cryptobot.strategy import sma_crossover  # noqa: F401
from cryptobot.strategy import rsi  # noqa: F401
from cryptobot.strategy import donchian  # noqa: F401
from cryptobot.strategy import donchian_adx  # noqa: F401
from cryptobot.strategy import orb  # noqa: F401
from cryptobot.strategy import bollinger  # noqa: F401
from cryptobot.strategy import volume_signal  # noqa: F401
from cryptobot.strategy import ensemble  # noqa: F401

__all__ = ["Strategy", "StrategyContext", "register_strategy", "get_strategy"]
