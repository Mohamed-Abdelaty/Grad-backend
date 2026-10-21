"""Plugin package: importing this module registers all built-in plugins."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from engine.plugins.sinks import console_sink, csv_sink, json_sink, postgres_sink  # noqa: F401
from engine.plugins.sources import csv_source, json_source, postgres_source, rest_api_source  # noqa: F401
from engine.plugins.transforms import (  # noqa: F401
    aggregate_transform,
    cast_transform,
    dedup_transform,
    filter_transform,
    join_transform,
    map_transform,
)
from engine.registry import catalog, lookup, registry  # noqa: F401


def discover_plugins() -> None:
    """Auto-discover and load any dynamically added plugins in subpackages."""
    pkg_dir = Path(__file__).parent
    for sub in ("sources", "transforms", "sinks"):
        sub_path = pkg_dir / sub
        if sub_path.is_dir():
            for mod in pkgutil.iter_modules([str(sub_path)]):
                if not mod.name.startswith("_"):
                    importlib.import_module(f"engine.plugins.{sub}.{mod.name}")


discover_plugins()
