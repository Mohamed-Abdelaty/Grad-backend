"""JSON file sink (JSON array output)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseSink, Batch
from engine.registry import register_sink


@register_sink("json")
class JsonSink(BaseSink):
    """Write records as a JSON array to ``path``.

    Modes: ``overwrite`` (default) writes ``[...]`` fresh; ``append`` merges
    with an existing array if the file already holds one, otherwise appends
    line-delimited records fallback. All buffered records are flushed on
    ``close()`` so the file is always valid JSON.
    """

    name = "json"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._records: list[dict[str, Any]] = []

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "mode": {"type": "string", "enum": ["overwrite", "append"], "default": "overwrite"},
                "encoding": {"type": "string", "default": "utf-8"},
                "indent": {"type": "integer", "default": 2},
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        if not self.config.get("path") or not isinstance(self.config.get("path"), str):
            raise PluginConfigError("?", "json sink requires 'path' (string)")
        if self.config.get("mode", "overwrite") not in ("overwrite", "append"):
            raise PluginConfigError("?", "json sink 'mode' must be overwrite|append")

    def open(self) -> None:
        self.validate_config()
        path = Path(str(self.config["path"]))
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        if self.config.get("mode", "overwrite") == "append" and path.is_file():
            try:
                existing = json.loads(
                    path.read_text(encoding=str(self.config.get("encoding", "utf-8")))
                )
                if isinstance(existing, list):
                    self._records = [r for r in existing if isinstance(r, dict)]
            except (ValueError, OSError):
                self._records = []
        else:
            self._records = []
        self._opened = True

    def write(self, batch: Batch) -> int:
        if not self._opened:
            raise RuntimeError("json sink is not open (call open() first)")
        self._records.extend(dict(r) for r in batch)
        return len(batch)

    def close(self) -> None:
        try:
            if self._opened:
                path = Path(str(self.config["path"]))
                path.write_text(
                    json.dumps(
                        self._records, indent=int(self.config.get("indent", 2)), default=str
                    ),
                    encoding=str(self.config.get("encoding", "utf-8")),
                )
        finally:
            self._opened = False
