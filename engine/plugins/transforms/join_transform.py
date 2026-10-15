"""Join transform (multi-input DAG node)."""

from __future__ import annotations

from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseTransform, Batch
from engine.registry import register_transform


@register_transform("join")
class JoinTransform(BaseTransform):
    """Join exactly two upstream inputs.

    Inputs: ``process_many({"left_id": [...], "right_id": [...]})``. Which
    predecessor is "left" vs "right" is set explicitly::

        {"left": "node_a", "right": "node_b",
         "left_on": "id", "right_on": "id", "how": "inner"}

    or with a shared key: ``{"keys": ["id"]}``. ``how`` is one of
    ``inner`` | ``left`` | ``right`` | ``outer``. Output semantics: every
    matched pair merges into one record (right-side keys that duplicate the
    join key are dropped; other collisions are prefixed ``right_``). Order:
    left-records order, then right-only rows for right/outer joins.
    """

    name = "join"
    multi_input = True

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "left": {"type": "string"},
                "right": {"type": "string"},
                "left_on": {"type": "string"},
                "right_on": {"type": "string"},
                "keys": {"type": "array", "items": {"type": "string"}},
                "how": {
                    "type": "string",
                    "enum": ["inner", "left", "right", "outer"],
                    "default": "inner",
                },
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        how = self.config.get("how", "inner")
        if how not in ("inner", "left", "right", "outer"):
            raise PluginConfigError("?", "join 'how' must be inner|left|right|outer")
        keys = self.config.get("keys")
        left_on, right_on = self.config.get("left_on"), self.config.get("right_on")
        if keys is not None:
            if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
                raise PluginConfigError("?", "join 'keys' must be a non-empty list of strings")
        elif not (isinstance(left_on, str) and isinstance(right_on, str)):
            raise PluginConfigError("?", "join requires 'keys' or both 'left_on' and 'right_on'")

    def _key_names(self) -> tuple[list[str], list[str]]:
        if self.config.get("keys"):
            keys = list(self.config["keys"])
            return keys, keys
        return [str(self.config["left_on"])], [str(self.config["right_on"])]

    def process(self, batch: Batch) -> Batch:
        raise PluginConfigError("?", "join is a multi-input node: wire exactly two upstream edges")

    def process_many(self, inputs: dict[str, Batch]) -> Batch:
        if len(inputs) != 2:
            raise PluginConfigError("?", f"join expects exactly 2 inputs, got {len(inputs)}")
        left_name = self.config.get("left")
        right_name = self.config.get("right")
        ids = list(inputs.keys())
        left_id = left_name if left_name in inputs else ids[0]
        right_id = right_name if right_name in inputs else ids[1]
        if left_id == right_id:
            left_id, right_id = ids[0], ids[1]
        left_rows, right_rows = inputs[left_id], inputs[right_id]
        left_keys, right_keys = self._key_names()
        how = str(self.config.get("how", "inner"))

        def key_of(row: dict[str, Any], keys: list[str]) -> tuple[Any, ...]:
            return tuple(row.get(k) for k in keys)

        right_index: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in right_rows:
            right_index.setdefault(key_of(row, right_keys), []).append(row)
        matched_right: set[int] = set()
        out: Batch = []
        for lrow in left_rows:
            matches = right_index.get(key_of(lrow, left_keys), [])
            if matches:
                for rrow in matches:
                    matched_right.add(id(rrow))
                    out.append(self._merge(lrow, rrow, set(right_keys)))
            elif how in ("left", "outer"):
                out.append(dict(lrow))
        if how in ("right", "outer"):
            for rrow in right_rows:
                if id(rrow) not in matched_right:
                    out.append(dict(rrow))
        return out

    @staticmethod
    def _merge(
        left: dict[str, Any], right: dict[str, Any], right_key_cols: set[str]
    ) -> dict[str, Any]:
        merged = dict(left)
        for key, value in right.items():
            if key in right_key_cols and key in merged:
                continue
            if key in merged:
                merged[f"right_{key}"] = value
            else:
                merged[key] = value
        return merged
