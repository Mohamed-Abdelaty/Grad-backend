"""Transform plugin tests (all six required operations)."""

from __future__ import annotations

import pytest

from engine.errors import PluginConfigError
from engine.plugins.transforms.aggregate_transform import AggregateTransform
from engine.plugins.transforms.cast_transform import CastTransform
from engine.plugins.transforms.dedup_transform import DeduplicateTransform
from engine.plugins.transforms.filter_transform import FilterTransform
from engine.plugins.transforms.join_transform import JoinTransform
from engine.plugins.transforms.map_transform import MapTransform


def test_filter_basic() -> None:
    plugin = FilterTransform({"expression": "amount > 100"})
    plugin.validate_config()
    rows = [{"amount": "50"}, {"amount": "150"}, {"amount": None}]
    assert plugin.process(rows) == [{"amount": "150"}]


def test_filter_unsafe_rejected() -> None:
    with pytest.raises(PluginConfigError):
        FilterTransform({"expression": "__import__('os')"}).validate_config()


def test_map_rename_and_select() -> None:
    plugin = MapTransform({"mapping": {"a": "alpha"}, "select": ["alpha"]})
    plugin.validate_config()
    assert plugin.process([{"a": 1, "b": 2}]) == [{"alpha": 1}]


def test_map_empty_mapping_rejected() -> None:
    with pytest.raises(PluginConfigError):
        MapTransform({"mapping": {}}).validate_config()


def test_cast_types_and_skip() -> None:
    plugin = CastTransform({"fields": {"n": "int", "f": "float", "s": "str", "b": "bool"}})
    plugin.validate_config()
    out = plugin.process([{"n": "3", "f": "1.5", "s": 7, "b": "yes"}])
    assert out == [{"n": 3, "f": 1.5, "s": "7", "b": True}]


def test_cast_fail_fast_vs_skip() -> None:
    with pytest.raises(ValueError, match="cast failed"):
        CastTransform({"fields": {"n": "int"}}).process([{"n": "abc"}])
    skipped = CastTransform({"fields": {"n": "int"}, "on_error": "skip"}).process([{"n": "abc"}])
    assert skipped == []


def test_cast_bad_target_rejected() -> None:
    with pytest.raises(PluginConfigError):
        CastTransform({"fields": {"n": "date"}}).validate_config()


def test_aggregate_shorthand_and_list() -> None:
    rows = [
        {"c": "a", "v": 1},
        {"c": "a", "v": 2},
        {"c": "b", "v": 5},
    ]
    short = AggregateTransform({"group_by": ["c"], "operation": "sum", "field": "v", "as": "total"})
    short.validate_config()
    short.process(rows)
    assert sorted(short.finish(), key=lambda r: r["c"]) == [
        {"c": "a", "total": 3},
        {"c": "b", "total": 5},
    ]
    listed = AggregateTransform(
        {
            "group_by": ["c"],
            "aggregations": [{"field": "v", "op": "avg", "as": "mean"}, {"op": "count", "as": "n"}],
        }
    )
    listed.validate_config()
    listed.process(rows)
    assert {r["c"]: r for r in listed.finish()}["a"] == {"c": "a", "mean": 1.5, "n": 2}


def test_aggregate_requires_operation() -> None:
    with pytest.raises(PluginConfigError):
        AggregateTransform({"group_by": ["c"]}).validate_config()


def test_join_inner_left_right_outer() -> None:
    left = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    right = [{"id": 2, "w": "x"}, {"id": 3, "w": "y"}]
    inner = JoinTransform({"keys": ["id"], "how": "inner"})
    inner.validate_config()
    assert inner.process_many({"l": left, "r": right}) == [{"id": 2, "v": "b", "w": "x"}]
    assert (
        len(JoinTransform({"keys": ["id"], "how": "left"}).process_many({"l": left, "r": right}))
        == 2
    )
    assert (
        len(JoinTransform({"keys": ["id"], "how": "right"}).process_many({"l": left, "r": right}))
        == 2
    )
    assert (
        len(JoinTransform({"keys": ["id"], "how": "outer"}).process_many({"l": left, "r": right}))
        == 3
    )


def test_join_named_inputs_and_keys() -> None:
    plugin = JoinTransform({"left": "a", "right": "b", "left_on": "x", "right_on": "y"})
    plugin.validate_config()
    out = plugin.process_many({"a": [{"x": 1}], "b": [{"y": 1, "z": 2}]})
    assert out == [{"x": 1, "y": 1, "z": 2}]  # both join keys preserved (different names)


def test_join_requires_two_inputs() -> None:
    with pytest.raises(PluginConfigError):
        JoinTransform({"keys": ["id"]}).process_many({"only": []})


def test_dedup_within_and_across_batches() -> None:
    plugin = DeduplicateTransform({"keys": ["id"]})
    plugin.validate_config()
    assert plugin.process([{"id": 1}, {"id": 1}, {"id": 2}]) == [{"id": 1}, {"id": 2}]
    assert plugin.process([{"id": 2}, {"id": 3}]) == [{"id": 3}]  # state persists


def test_dedup_whole_record_default() -> None:
    plugin = DeduplicateTransform({})
    assert plugin.process([{"a": 1}, {"a": 1}]) == [{"a": 1}]
