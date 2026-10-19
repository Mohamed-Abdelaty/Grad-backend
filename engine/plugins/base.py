"""Abstract plugin interfaces.

Contract for every plugin category:

- ``__init__(config)`` stores raw config (no I/O).
- :meth:`validate_config` raises :class:`PluginConfigError` on bad config.
  Called before any resource is opened and again by the executor.
- :meth:`open` acquires resources (files, connections, HTTP sessions).
- :meth:`close` releases resources. Always called by the executor via
  ``try/finally``, including on failure and cancellation, so implementations
  must be idempotent and must tolerate ``close()`` without ``open()``.
- Processing entry points differ per category (adapted signatures):
  - sources: :meth:`BaseSource.read` yields :data:`Batch` lists of
    :data:`Record`. :meth:`BaseSource.process` is an alias kept so all
    categories expose the ``validate_config/open/process/close`` lifecycle.
  - transforms: :meth:`BaseTransform.process` maps one batch to a new
    batch. Multi-input nodes (join) override :meth:`BaseTransform.process_many`
    which receives ``{predecessor_id: records}``.
  - sinks: :meth:`BaseSink.write` consumes one batch.
    :meth:`BaseSink.process` is an alias for ``write``.

Every concrete plugin must also implement :meth:`config_schema` returning
the JSON Schema of its own config dict. The ``GET /plugins`` catalog and
pipeline validation both use these schemas, so no second catalog can drift.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

Record = dict[str, Any]
Batch = list[Record]


class BasePlugin(ABC):
    """Shared lifecycle for all plugins."""

    #: config key description used for error messages; override per plugin.
    name: ClassVar[str] = "base"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._opened = False

    @classmethod
    @abstractmethod
    def config_schema(cls) -> dict[str, Any]:
        """Return the JSON Schema describing this plugin's ``config`` dict."""
        raise NotImplementedError

    @abstractmethod
    def validate_config(self) -> None:
        """Raise :class:`PluginConfigError` if ``self.config`` is invalid."""
        raise NotImplementedError

    def open(self) -> None:
        """Acquire external resources. Default: mark opened (no I/O)."""
        self._opened = True

    def close(self) -> None:
        """Release external resources. Must be safe to call multiple times."""
        self._opened = False

    @property
    def is_open(self) -> bool:
        """Whether :meth:`open` has been called without a matching close."""
        return self._opened


class BaseSource(BasePlugin):
    """A pipeline entry point producing batches of records."""

    @abstractmethod
    def read(self) -> Any:
        """Yield :data:`Batch` objects (list of record dicts)."""
        raise NotImplementedError
        yield  # pragma: no cover - makes this a generator for typing

    def process(self) -> Any:
        """Lifecycle alias for :meth:`read` (source ``process`` step)."""
        return self.read()


class BaseTransform(BasePlugin):
    """A per-batch record transformation."""

    #: Set True when the transform needs all upstream inputs at once (join).
    multi_input: ClassVar[bool] = False

    @abstractmethod
    def process(self, batch: Batch) -> Batch:
        """Transform one batch into a new batch (no in-place mutation)."""
        raise NotImplementedError

    def process_many(self, inputs: dict[str, Batch]) -> Batch:
        """Transform multiple upstream outputs (default: concatenate + process)."""
        merged: Batch = []
        for records in inputs.values():
            merged.extend(records)
        return self.process(merged)


class BaseSink(BasePlugin):
    """A pipeline exit point consuming batches of records."""

    @abstractmethod
    def write(self, batch: Batch) -> int:
        """Consume one batch; return the number of records written."""
        raise NotImplementedError

    def process(self, batch: Batch) -> int:
        """Lifecycle alias for :meth:`write` (sink ``process`` step)."""
        return self.write(batch)

from .base_compat import Plugin
