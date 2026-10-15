"""CSV file sink."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseSink, Batch
from engine.registry import register_sink


@register_sink("csv")
class CsvSink(BaseSink):
    """Write records to a CSV file.

    Config: ``{"path": ..., "mode": "overwrite"|"append"}``. Header is the
    union of keys in insertion order, captured from the first batch; later
    batches with new columns extend subsequent rows (earlier rows keep empty
    values for those columns only in file order -- documented limitation for
    heterogeneous schemas).
    """

    name = "csv"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._writer: Any = None
        self._fh: Any = None
        self._fieldnames: list[str] = []
        self._written = 0

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "mode": {"type": "string", "enum": ["overwrite", "append"], "default": "overwrite"},
                "encoding": {"type": "string", "default": "utf-8"},
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        if not self.config.get("path") or not isinstance(self.config.get("path"), str):
            raise PluginConfigError("?", "csv sink requires 'path' (string)")
        if self.config.get("mode", "overwrite") not in ("overwrite", "append"):
            raise PluginConfigError("?", "csv sink 'mode' must be overwrite|append")

    def open(self) -> None:
        self.validate_config()
        path = Path(str(self.config["path"]))
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if self.config.get("mode", "overwrite") == "overwrite" else "a"
        self._fh = path.open(mode, newline="", encoding=str(self.config.get("encoding", "utf-8")))
        self._opened = True

    def write(self, batch: Batch) -> int:
        if not self._opened or self._fh is None:
            raise RuntimeError("csv sink is not open (call open() first)")
        if not batch:
            return 0
        for record in batch:
            for key in record:
                if key not in self._fieldnames:
                    self._fieldnames.append(key)
        if self._writer is None:
            self._writer = csv.DictWriter(
                self._fh, fieldnames=self._fieldnames, extrasaction="ignore"
            )
            if self._fh.tell() == 0:
                self._writer.writeheader()
        # csv.DictWriter fixes fieldnames at creation; recreate if grown.
        if list(self._writer.fieldnames or []) != self._fieldnames:
            self._writer = csv.DictWriter(
                self._fh, fieldnames=self._fieldnames, extrasaction="ignore"
            )
        for record in batch:
            self._writer.writerow({k: record.get(k, "") for k in self._fieldnames})
        self._written += len(batch)
        return len(batch)

    def close(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
        finally:
            self._fh = None
            self._writer = None
            self._opened = False
