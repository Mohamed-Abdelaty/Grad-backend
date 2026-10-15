"""JSON source supporting JSON Lines and JSON arrays."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseSource, Batch
from engine.registry import register_source


@register_source("json")
class JsonSource(BaseSource):
    """Read records from a ``.json``/``.jsonl`` file.

    Both formats are accepted:
    - JSON Lines: one JSON object per line (``.jsonl`` or ``.json``)
    - JSON array: a single top-level ``[...]`` array of objects
    Malformed lines raise a descriptive error naming the line number.
    """

    name = "json"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "encoding": {"type": "string", "default": "utf-8"},
                "batch_size": {"type": "integer", "minimum": 1, "default": 500},
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        path = self.config.get("path")
        if not path or not isinstance(path, str):
            raise PluginConfigError("?", "json source requires 'path' (string)")
        batch_size = self.config.get("batch_size", 500)
        if not isinstance(batch_size, int) or batch_size < 1:
            raise PluginConfigError("?", "json 'batch_size' must be a positive integer")

    def read(self) -> Iterator[Batch]:
        path = Path(str(self.config["path"]))
        if not path.is_file():
            raise FileNotFoundError(f"JSON source file not found: {path}")
        encoding = str(self.config.get("encoding", "utf-8"))
        batch_size = int(self.config.get("batch_size", 500))
        try:
            text = path.read_text(encoding=encoding)
        except OSError as exc:
            raise OSError(f"Failed reading JSON '{path}': {exc}") from exc
        stripped = text.lstrip()
        records: list[dict[str, Any]]
        if stripped.startswith("["):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON array in '{path}': {exc}") from exc
            if not isinstance(data, list):
                raise ValueError(f"Top-level JSON in '{path}' must be an array of objects")
            records = []
            for i, item in enumerate(data):
                if not isinstance(item, dict):
                    raise ValueError(f"JSON array item {i} in '{path}' is not an object")
                records.append(item)
        else:
            records = []
            for lineno, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON on line {lineno} of '{path}': {exc}") from exc
                if not isinstance(item, dict):
                    raise ValueError(f"JSON line {lineno} in '{path}' is not an object")
                records.append(item)
        batch: Batch = []
        for record in records:
            batch.append(record)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
