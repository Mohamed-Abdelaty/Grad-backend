"""Typed engine errors.

All engine failures use these typed exceptions so callers (executor, CLI,
API) can report the failing node id, error type, and details consistently.
"""

from __future__ import annotations

from typing import Any


class EngineError(Exception):
    """Base class for all engine errors."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details: dict[str, object] = details or {}


class ConfigValidationError(EngineError):
    """Pipeline configuration is invalid."""

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, object] | None = None,
        errors: list[str] | None = None,
    ) -> None:
        if details is None and errors is not None:
            details = {"errors": errors}
        elif details is not None and errors is not None:
            details["errors"] = errors
        super().__init__(message, details=details)
        raw_errors = details.get("errors") if details else None
        self.errors: list[str] = errors or (
            [str(x) for x in raw_errors] if isinstance(raw_errors, list) else []
        )


class UnknownPluginError(EngineError):
    """A node references a plugin name that is not registered."""

    def __init__(self, category: str, name: str) -> None:
        super().__init__(
            f"Unknown {category} plugin: '{name}'",
            details={"category": category, "plugin": name},
        )
        self.category = category
        self.plugin_name = name


class PluginConfigError(EngineError):
    """A node's plugin configuration is invalid."""

    def __init__(
        self, node_id: str, message: str, *, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(
            f"Node '{node_id}': {message}", details={"node_id": node_id, **(details or {})}
        )
        self.node_id = node_id


class NodeExecutionError(EngineError):
    """A node failed during execution (after retries are exhausted)."""

    def __init__(
        self,
        node_id: str,
        message: str,
        *,
        error_type: str = "NodeExecutionError",
        attempts: int = 1,
        metrics: dict[str, Any] | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(f"Node '{node_id}' failed: {message}", details=details)
        self.node_id = node_id
        self.error_type = error_type
        self.attempts = attempts
        self.metrics: dict[str, Any] = metrics or {}


class CancellationError(EngineError):
    """Execution was cancelled cooperatively."""

    def __init__(self, message: str = "Execution cancelled") -> None:
        super().__init__(message)
