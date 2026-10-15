"""Cast field types transform."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseTransform, Batch
from engine.registry import register_transform

_ALLOWED_TYPES = ("int", "float", "str", "bool")


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "y", "t"):
            return True
        if text in ("false", "0", "no", "n", "f", ""):
            return False
    raise ValueError(f"Cannot cast {value!r} to bool")


_CASTERS: dict[str, Callable[[Any], Any]] = {
    "int": lambda v: (
        int(float(v))
        if isinstance(v, str) and v.strip().replace(".", "", 1).lstrip("-").isdigit() and "." in v
        else int(v)
    ),
    "float": float,
    "str": str,
    "bool": _to_bool,
}


@register_transform("cast")
class CastTransform(BaseTransform):
    """Cast fields to a new type: ``{"fields": {"amount": "float"}}``.

    Supported targets: ``int`` / ``float`` / ``str`` / ``bool``. ``None``
    values stay ``None``. Uncastable values raise a per-record error that
    the executor attributes to this node (record is counted as errored and
    skipped only if ``on_error: "skip"``; default is to fail fast).
    """

    name = "cast"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["fields"],
            "properties": {
                "fields": {"type": "object", "additionalProperties": {"type": "string"}},
                "on_error": {"type": "string", "enum": ["fail", "skip"], "default": "fail"},
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        fields = self.config.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise PluginConfigError("?", "cast requires a non-empty 'fields' object")
        for col, target in fields.items():
            if target not in _ALLOWED_TYPES:
                raise PluginConfigError(
                    "?", f"cast target for '{col}' must be one of {_ALLOWED_TYPES}"
                )
        if self.config.get("on_error", "fail") not in ("fail", "skip"):
            raise PluginConfigError("?", "cast 'on_error' must be 'fail' or 'skip'")

    def process(self, batch: Batch) -> Batch:
        fields: dict[str, str] = dict(self.config["fields"])
        on_error = str(self.config.get("on_error", "fail"))
        out: Batch = []
        for record in batch:
            new_record = dict(record)
            try:
                for col, target in fields.items():
                    if col not in new_record or new_record[col] is None or new_record[col] == "":
                        new_record[col] = None
                        continue
                    new_record[col] = _CASTERS[target](new_record[col])
            except (ValueError, TypeError) as exc:
                if on_error == "skip":
                    continue
                raise ValueError(f"cast failed: {exc}") from exc
            out.append(new_record)
        return out
