from __future__ import annotations

import pytest

# Importing the package ensures the starter strategy registers itself.
import cryptobot.strategy.sma_crossover  # noqa: F401
from cryptobot.strategy.registry import get_strategy, registered_names


def test_sma_crossover_registered():
    assert "sma_crossover" in registered_names()
    cls = get_strategy("sma_crossover")
    assert cls.name == "sma_crossover"


def test_unknown_strategy_raises():
    with pytest.raises(KeyError):
        get_strategy("does_not_exist")
