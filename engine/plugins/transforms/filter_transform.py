"""Filter transform using the safe expression grammar."""

from __future__ import annotations

from typing import Any

from engine.errors import PluginConfigError
from engine.expression import UnsafeExpressionError, compile_expression, evaluate
from engine.plugins.base import BaseTransform, Batch
from engine.registry import register_transform


@register_transform("filter")
class FilterTransform(BaseTransform):
    """Keep records where ``expression`` evaluates truthy.

    See :mod:`engine.expression` for the allowed grammar. Unrestricted
    ``eval()`` is never used.
    """

    name = "filter"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["expression"],
            "properties": {"expression": {"type": "string"}},
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        expr = self.config.get("expression")
        if not expr or not isinstance(expr, str):
            raise PluginConfigError("?", "filter requires 'expression' (string)")
        try:
            compile_expression(expr)
        except UnsafeExpressionError as exc:
            raise PluginConfigError("?", f"unsafe filter expression: {exc}") from exc

    def process(self, batch: Batch) -> Batch:
        expr = str(self.config["expression"])
        out: Batch = []
        for record in batch:
            try:
                if evaluate(expr, record):
                    out.append(dict(record))
            except UnsafeExpressionError:
                raise
            except Exception:
                # A record that cannot be evaluated (e.g. type mismatch in
                # arithmetic) does not match rather than failing the batch.
                continue
        return out
