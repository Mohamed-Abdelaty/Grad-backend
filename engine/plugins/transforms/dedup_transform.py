"""Deduplicate transform."""

from __future__ import annotations

from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseTransform, Batch
from engine.registry import register_transform


@register_transform("deduplicate")
class DeduplicateTransform(BaseTransform):
    """Drop duplicate records.

    Config: ``{"keys": ["col", ...]}`` (default: whole record). Keeps the
    first occurrence. State (seen keys) persists across batches within one
    run, so duplicates split across batches are still removed.
    """

    name = "deduplicate"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._seen: set[str] = set()

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        keys = self.config.get("keys")
        if keys is not None and (
            not isinstance(keys, list) or not all(isinstance(k, str) for k in keys)
        ):
            raise PluginConfigError("?", "deduplicate 'keys' must be a list of strings")

    def _fingerprint(self, record: dict[str, Any]) -> str:
        keys = self.config.get("keys")
        items = [(k, record.get(k)) for k in keys] if keys else sorted(record.items())
        return repr([(k, v) for k, v in items])

    def process(self, batch: Batch) -> Batch:
        out: Batch = []
        for record in batch:
            fp = self._fingerprint(record)
            if fp in self._seen:
                continue
            self._seen.add(fp)
            out.append(dict(record))
        return out
