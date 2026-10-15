"""Plugin package: importing this module registers all built-in plugins."""

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
