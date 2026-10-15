"""Decorator-based plugin registry.

Plugins register themselves by category (source/transform/sink) and name::

    @register_source("csv")
    class CsvSource(BaseSource):
        ...

The plugin catalog endpoint (:code:`GET /plugins`), config validation, and
the executor all resolve through this registry, so the catalog can never
drift from the implementations used at runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from engine.errors import UnknownPluginError

if TYPE_CHECKING:  # pragma: no cover
    from engine.plugins.base import BaseSink, BaseSource, BaseTransform

_REGISTRY: dict[tuple[str, str], type] = {}

T = TypeVar("T")


def _register(category: str, name: str) -> Callable[[T], T]:
    def decorator(cls: T) -> T:
        key = (category, name)
        if key in _REGISTRY:
            raise ValueError(f"Plugin already registered: {category}/{name}")
        _REGISTRY[key] = cls  # type: ignore[assignment]
        return cls

    return decorator


def register_source(name: str) -> Callable[[T], T]:
    """Register a source plugin under ``name``."""
    return _register("source", name)


def register_transform(name: str) -> Callable[[T], T]:
    """Register a transform plugin under ``name``."""
    return _register("transform", name)


def register_sink(name: str) -> Callable[[T], T]:
    """Register a sink plugin under ``name``."""
    return _register("sink", name)


def lookup(category: str, name: str) -> type:
    """Return the plugin class for ``category``/``name``.

    Raises:
        UnknownPluginError: if no plugin is registered under that name.
    """
    try:
        return _REGISTRY[(category, name)]
    except KeyError:
        raise UnknownPluginError(category, name) from None


def lookup_source(name: str) -> type[BaseSource]:
    """Look up a source plugin by name."""
    return lookup("source", name)  # type: ignore[return-value]


def lookup_transform(name: str) -> type[BaseTransform]:
    """Look up a transform plugin by name."""
    return lookup("transform", name)  # type: ignore[return-value]


def lookup_sink(name: str) -> type[BaseSink]:
    """Look up a sink plugin by name."""
    return lookup("sink", name)  # type: ignore[return-value]


def catalog() -> list[dict[str, Any]]:
    """Return catalog entries derived from the registered plugins.

    Each entry: ``{"category", "name", "config_schema"}`` where
    ``config_schema`` comes from the plugin class itself.
    """
    entries: list[dict[str, Any]] = []
    for (category, name), cls in sorted(_REGISTRY.items()):
        schema_fn = getattr(cls, "config_schema", None)
        schema: dict[str, Any] = schema_fn() if callable(schema_fn) else {}
        entries.append({"category": category, "name": name, "config_schema": schema})
    return entries


def clear() -> None:  # pragma: no cover
    """Clear the registry (used only in tests that register fakes)."""
    _REGISTRY.clear()
