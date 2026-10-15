"""CSV file source."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseSource, Batch
from engine.registry import register_source


@register_source("csv")
class CsvSource(BaseSource):
    """Read records from a CSV file (one record per row)."""

    name = "csv"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string", "description": "Path to the CSV file"},
                "delimiter": {"type": "string", "default": ","},
                "encoding": {"type": "string", "default": "utf-8"},
                "batch_size": {"type": "integer", "minimum": 1, "default": 500},
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        cfg = self.config
        path = cfg.get("path")
        if not path or not isinstance(path, str):
            raise PluginConfigError("?", "csv source requires 'path' (string)")
        delimiter = cfg.get("delimiter", ",")
        if not isinstance(delimiter, str) or len(delimiter) != 1:
            raise PluginConfigError("?", "csv 'delimiter' must be a single character")
        batch_size = cfg.get("batch_size", 500)
        if not isinstance(batch_size, int) or batch_size < 1:
            raise PluginConfigError("?", "csv 'batch_size' must be a positive integer")

    def read(self) -> Iterator[Batch]:
        path = Path(str(self.config["path"]))
        if not path.is_file():
            raise FileNotFoundError(f"CSV source file not found: {path}")
        delimiter = str(self.config.get("delimiter", ","))
        encoding = str(self.config.get("encoding", "utf-8"))
        batch_size = int(self.config.get("batch_size", 500))
        batch: Batch = []
        try:
            with path.open("r", newline="", encoding=encoding) as fh:
                reader = csv.DictReader(fh, delimiter=delimiter)
                if reader.fieldnames is None:
                    return
                for row in reader:
                    # csv.DictReader may produce None for short rows; normalize.
                    batch.append(
                        {k: ("" if v is None else v) for k, v in row.items() if k is not None}
                    )
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise OSError(f"Failed reading CSV '{path}': {exc}") from exc
        if batch:
            yield batch
