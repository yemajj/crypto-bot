"""Name-based strategy registry.

Strategies register themselves via the `@register_strategy` decorator. The
runner looks them up by the `name` in the YAML config, so swapping strategies
is a config change, not a code change.
"""

from __future__ import annotations

from typing import TypeVar

from cryptobot.strategy.base import Strategy

T = TypeVar("T", bound=type[Strategy])

_REGISTRY: dict[str, type[Strategy]] = {}


def register_strategy(name: str):
    """Class decorator that registers a Strategy subclass under `name`."""

    def _decorate(cls: T) -> T:
        if name in _REGISTRY:
            raise ValueError(f"Strategy already registered: {name}")
        cls.name = name  # type: ignore[attr-defined]
        _REGISTRY[name] = cls
        return cls

    return _decorate


def get_strategy(name: str) -> type[Strategy]:
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown strategy '{name}'. Registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def registered_names() -> list[str]:
    return sorted(_REGISTRY)
