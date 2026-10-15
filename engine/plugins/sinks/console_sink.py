"""Console sink (stdout / captured)."""

from __future__ import annotations

import contextlib
import sys
from typing import Any, TextIO

from engine.errors import PluginConfigError
from engine.plugins.base import BaseSink, Batch
from engine.registry import register_sink


@register_sink("console")
class ConsoleSink(BaseSink):
    """Write records to stdout (or a captured in-memory list for tests).

    Config: ``{"prefix": "..."}``. Written records are also kept in
    :attr:`written` so tests and the API worker can report counters without
    capturing stdout.
    """

    name = "console"

    def __init__(self, config: dict[str, Any] | None = None, stream: TextIO | None = None) -> None:
        super().__init__(config)
        self._stream = stream
        self.written: list[dict[str, Any]] = []

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"prefix": {"type": "string", "default": ""}},
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        prefix = self.config.get("prefix", "")
        if not isinstance(prefix, str):
            raise PluginConfigError("?", "console sink 'prefix' must be a string")

    def write(self, batch: Batch) -> int:
        if not self._opened:
            raise RuntimeError("console sink is not open (call open() first)")
        stream = self._stream or sys.stdout
        prefix = str(self.config.get("prefix", ""))
        for record in batch:
            stream.write(f"{prefix}{record}\n")
            self.written.append(dict(record))
        with contextlib.suppress(Exception):
            stream.flush()
        return len(batch)
