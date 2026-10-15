"""Map / rename-columns transform."""

from __future__ import annotations

from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseTransform, Batch
from engine.registry import register_transform


@register_transform("map")
class MapTransform(BaseTransform):
    """Rename columns via ``mapping`` ({old_name: new_name}).

    Config also accepts ``select`` (keep only these output columns) and
    ``drop`` (remove these input columns first). Unknown columns in
    ``mapping`` are ignored; collisions (two inputs mapped to the same
    output) raise an error at runtime.
    """

    name = "map"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["mapping"],
            "properties": {
                "mapping": {"type": "object", "additionalProperties": {"type": "string"}},
                "select": {"type": "array", "items": {"type": "string"}},
                "drop": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        mapping = self.config.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            raise PluginConfigError("?", "map requires a non-empty 'mapping' object")
        for old, new in mapping.items():
            if not isinstance(old, str) or not isinstance(new, str) or not new:
                raise PluginConfigError("?", "map 'mapping' must be {old_name: new_name} strings")
        for key in ("select", "drop"):
            val = self.config.get(key)
            if val is not None and (
                not isinstance(val, list) or not all(isinstance(v, str) for v in val)
            ):
                raise PluginConfigError("?", f"map '{key}' must be a list of strings")

    def process(self, batch: Batch) -> Batch:
        mapping: dict[str, str] = dict(self.config["mapping"])
        drop = set(self.config.get("drop") or [])
        select = self.config.get("select")
        targets = list(mapping.values())
        if len(set(targets)) != len(targets):
            raise RuntimeError("map 'mapping' must not map two columns to the same output name")
        out: Batch = []
        for record in batch:
            renamed: Batch = [{}]
            new_record: dict[str, Any] = {}
            for key, value in record.items():
                if key in drop:
                    continue
                new_record[mapping.get(key, key)] = value
            if select is not None:
                new_record = {k: new_record.get(k) for k in select}
            renamed[0] = new_record
            out.extend(renamed)
        return out
