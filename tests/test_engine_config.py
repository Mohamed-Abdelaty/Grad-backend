"""Engine config-contract tests: valid docs, structural errors, plugin config errors."""

from __future__ import annotations

import pytest

from engine.errors import ConfigValidationError, PluginConfigError, UnknownPluginError
from engine.executor import Executor, validate_pipeline_dict
from tests.conftest import _valid_doc


def test_valid_document_passes() -> None:
    doc = validate_pipeline_dict(_valid_doc())
    assert doc.pipeline.name == "test-pipe"
    assert len(doc.pipeline.nodes) == 3


def test_duplicate_node_ids_rejected() -> None:
    payload = _valid_doc()
    payload["pipeline"]["nodes"].append(
        {"id": "src_1", "type": "sink", "plugin": "console", "config": {}}
    )
    with pytest.raises(ConfigValidationError, match="Duplicate node ids"):
        validate_pipeline_dict(payload)


def test_edge_to_unknown_node_rejected() -> None:
    payload = _valid_doc()
    payload["pipeline"]["edges"].append({"from": "src_1", "to": "nope"})
    with pytest.raises(ConfigValidationError, match="unknown node"):
        validate_pipeline_dict(payload)


def test_self_loop_rejected() -> None:
    payload = _valid_doc()
    payload["pipeline"]["edges"] = [{"from": "src_1", "to": "src_1"}]
    with pytest.raises(ConfigValidationError, match="Self-loop"):
        validate_pipeline_dict(payload)


def test_cycle_rejected() -> None:
    payload = _valid_doc()
    payload["pipeline"]["edges"] = [
        {"from": "src_1", "to": "tr_1"},
        {"from": "tr_1", "to": "src_1"},
    ]
    with pytest.raises(ConfigValidationError, match="acyclic"):
        validate_pipeline_dict(payload)


def test_missing_source_rejected() -> None:
    payload = _valid_doc()
    payload["pipeline"]["nodes"] = [
        {
            "id": "tr_1",
            "type": "transform",
            "plugin": "filter",
            "config": {"expression": "amount > 1"},
        },
        {"id": "snk_1", "type": "sink", "plugin": "console", "config": {}},
    ]
    payload["pipeline"]["edges"] = [{"from": "tr_1", "to": "snk_1"}]
    with pytest.raises(ConfigValidationError, match="at least one source"):
        validate_pipeline_dict(payload)


def test_missing_sink_rejected() -> None:
    payload = _valid_doc()
    payload["pipeline"]["nodes"] = [
        {
            "id": "src_1",
            "type": "source",
            "plugin": "csv",
            "config": {"path": "examples/data/orders.csv"},
        },
    ]
    payload["pipeline"]["edges"] = []
    with pytest.raises(ConfigValidationError, match="at least one sink"):
        validate_pipeline_dict(payload)


def test_unknown_plugin_raises_typed_error() -> None:
    payload = _valid_doc()
    payload["pipeline"]["nodes"][0]["plugin"] = "nope"
    with pytest.raises(UnknownPluginError, match="Unknown source plugin"):
        validate_pipeline_dict(payload)


def test_invalid_node_type_rejected() -> None:
    payload = _valid_doc()
    payload["pipeline"]["nodes"][0]["type"] = "middle"
    with pytest.raises(ConfigValidationError):
        validate_pipeline_dict(payload)


def test_plugin_config_error_carries_node_id() -> None:
    payload = _valid_doc()
    payload["pipeline"]["nodes"][1]["config"] = {"expression": ""}
    with pytest.raises(PluginConfigError) as exc_info:
        validate_pipeline_dict(payload)
    assert exc_info.value.node_id == "tr_1"


def test_invalid_retry_rejected() -> None:
    payload = _valid_doc()
    payload["pipeline"]["retry"] = {"max_attempts": 0, "backoff_seconds": 5}
    with pytest.raises(ConfigValidationError):
        validate_pipeline_dict(payload)


def test_missing_top_level_pipeline_rejected() -> None:
    with pytest.raises(ConfigValidationError):
        validate_pipeline_dict({"nope": {}})


def test_executor_topological_order_respects_edges() -> None:
    doc = validate_pipeline_dict(_valid_doc())
    order = Executor(doc.pipeline).topological_order()
    assert order.index("src_1") < order.index("tr_1") < order.index("snk_1")


def test_document_missing_id_rejected() -> None:
    payload = _valid_doc()
    del payload["pipeline"]["id"]
    with pytest.raises(ConfigValidationError):
        validate_pipeline_dict(payload)


def test_field_level_error_details_present() -> None:
    payload = _valid_doc()
    payload["pipeline"]["nodes"][0]["config"] = {
        "path": "examples/data/orders.csv",
        "delimiter": ";;",
    }
    try:
        validate_pipeline_dict(payload)
        raise AssertionError("should have raised")
    except PluginConfigError as exc:
        assert exc.node_id == "src_1"
        assert "delimiter" in str(exc)
