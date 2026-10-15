"""Aggregate (group-by) transform."""

from __future__ import annotations

from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseTransform, Batch
from engine.registry import register_transform

_ALLOWED_OPS = ("sum", "avg", "min", "max", "count")


@register_transform("aggregate")
class AggregateTransform(BaseTransform):
    """Group records and compute aggregations.

    Two equivalent config shapes are accepted:

    - explicit list::

        {"group_by": ["customer_id"],
         "aggregations": [{"field": "amount", "op": "sum", "as": "total"}]}

    - shorthand for a single aggregation::

        {"group_by": ["customer_id"], "operation": "sum",
         "field": "amount", "as": "total"}
    """

    name = "aggregate"
    # Stateful across batches within one run: executor calls process() per
    # batch, so we buffer rows and emit results at close/flush time via the
    # executor's flush hook (see `finish`). For single-batch use this is a no-op.
    _buffered: list[dict[str, Any]]

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._buffer: list[dict[str, Any]] = []
        self._flushed = False

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "group_by": {"type": "array", "items": {"type": "string"}},
                "aggregations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["op"],
                        "properties": {
                            "field": {"type": "string"},
                            "op": {"type": "string", "enum": list(_ALLOWED_OPS)},
                            "as": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "operation": {"type": "string", "enum": list(_ALLOWED_OPS)},
                "field": {"type": "string"},
                "as": {"type": "string"},
            },
            "additionalProperties": False,
        }

    def _specs(self) -> tuple[list[str], list[dict[str, Any]]]:
        group_by = self.config.get("group_by") or []
        aggs = self.config.get("aggregations")
        if aggs is None:
            op = self.config.get("operation")
            if not op:
                raise PluginConfigError("?", "aggregate requires 'aggregations' or 'operation'")
            aggs = [{"field": self.config.get("field"), "op": op, "as": self.config.get("as")}]
        return list(group_by), list(aggs)

    def validate_config(self) -> None:
        group_by = self.config.get("group_by") or []
        if not isinstance(group_by, list) or not all(isinstance(c, str) for c in group_by):
            raise PluginConfigError("?", "aggregate 'group_by' must be a list of strings")
        _, aggs = self._specs()
        if not aggs:
            raise PluginConfigError("?", "aggregate requires at least one aggregation")
        for agg in aggs:
            if not isinstance(agg, dict) or agg.get("op") not in _ALLOWED_OPS:
                raise PluginConfigError("?", f"aggregation op must be one of {_ALLOWED_OPS}")
            if agg["op"] != "count" and not agg.get("field"):
                raise PluginConfigError("?", f"aggregation '{agg['op']}' requires 'field'")

    def process(self, batch: Batch) -> Batch:
        """Buffer batches; final rows are produced by :meth:`finish`."""
        self._buffer.extend(dict(r) for r in batch)
        return []

    def finish(self) -> Batch:
        """Compute grouped results over all buffered rows."""
        group_by, aggs = self._specs()
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in self._buffer:
            key = tuple(row.get(col) for col in group_by)
            groups.setdefault(key, []).append(row)
        out: Batch = []
        for key, rows in groups.items():
            result: dict[str, Any] = dict(zip(group_by, key, strict=True))
            for agg in aggs:
                result[agg.get("as") or f"{agg.get('field')}_{agg['op']}"] = self._compute(
                    agg, rows
                )
            out.append(result)
        self._buffer = []
        return out

    def _compute(self, agg: dict[str, Any], rows: list[dict[str, Any]]) -> Any:
        op = agg["op"]
        if op == "count":
            field = agg.get("field")
            if field:
                return sum(1 for r in rows if r.get(field) is not None)
            return len(rows)
        values = [r.get(agg["field"]) for r in rows]
        present = [v for v in values if v is not None]
        if not present:
            return None
        if op == "sum":
            return sum(present)
        if op == "avg":
            return sum(present) / len(present)
        if op == "min":
            return min(present)
        return max(present)  # max
