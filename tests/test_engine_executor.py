"""Executor tests: DAG routing, retry, metrics, events, cancellation, cleanup."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

import engine.plugins  # noqa: F401
from engine.errors import (
    CancellationError,
    NodeExecutionError,
    PluginConfigError,
)
from engine.executor import CancellationToken, ExecutionEvent, Executor, validate_pipeline_dict
from engine.plugins.base import BaseTransform, Batch
from engine.plugins.sources.csv_source import CsvSource
from engine.registry import register_source, register_transform
from tests.conftest import _valid_doc


def _doc(nodes: list[dict[str, Any]], edges: list[dict[str, str]], **retry: Any) -> dict[str, Any]:
    payload = _valid_doc()
    payload["pipeline"]["nodes"] = nodes
    payload["pipeline"]["edges"] = edges
    payload["pipeline"]["retry"] = {
        "max_attempts": retry.get("max_attempts", 1),
        "backoff_seconds": retry.get("backoff_seconds", 0),
    }
    return payload


def _csv_sink(path: str) -> dict[str, Any]:
    return {
        "id": "snk",
        "type": "sink",
        "plugin": "csv",
        "config": {"path": path, "mode": "overwrite"},
    }


def test_linear_run_metrics_and_event_order(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out = str(tmp_path / "clean.csv")
    payload = _doc(
        [
            {
                "id": "src",
                "type": "source",
                "plugin": "csv",
                "config": {"path": "examples/data/orders.csv"},
            },
            {
                "id": "tr",
                "type": "transform",
                "plugin": "filter",
                "config": {"expression": "amount > 100"},
            },
            {
                "id": "snk",
                "type": "sink",
                "plugin": "csv",
                "config": {"path": out, "mode": "overwrite"},
            },
        ],
        [{"from": "src", "to": "tr"}, {"from": "tr", "to": "snk"}],
    )
    events: list[ExecutionEvent] = []
    result = Executor(validate_pipeline_dict(payload).pipeline, event_callback=events.append).run()
    kinds = [e.type for e in events]
    assert kinds[0] == "node_started" and events[0].node_id == "src"
    assert kinds[-1] == "run_finished"
    assert result.metrics["src"].records_out == 5
    assert result.metrics["tr"].records_in == 5
    assert result.metrics["tr"].records_out == 3
    assert result.metrics["snk"].records_out == 3
    for metric in result.metrics.values():
        assert metric.attempts == 1 and metric.error_count == 0 and metric.duration_seconds >= 0


def test_branching_dag_routes_to_both_branches(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out_a, out_b = str(tmp_path / "a.csv"), str(tmp_path / "b.csv")
    payload = _doc(
        [
            {
                "id": "src",
                "type": "source",
                "plugin": "csv",
                "config": {"path": "examples/data/orders.csv"},
            },
            {
                "id": "hi",
                "type": "transform",
                "plugin": "filter",
                "config": {"expression": "amount > 100"},
            },
            {
                "id": "lo",
                "type": "transform",
                "plugin": "filter",
                "config": {"expression": "amount <= 100"},
            },
            {
                "id": "snk_a",
                "type": "sink",
                "plugin": "csv",
                "config": {"path": out_a, "mode": "overwrite"},
            },
            {
                "id": "snk_b",
                "type": "sink",
                "plugin": "csv",
                "config": {"path": out_b, "mode": "overwrite"},
            },
        ],
        [
            {"from": "src", "to": "hi"},
            {"from": "src", "to": "lo"},
            {"from": "hi", "to": "snk_a"},
            {"from": "lo", "to": "snk_b"},
        ],
    )
    result = Executor(validate_pipeline_dict(payload).pipeline).run()
    assert result.metrics["snk_a"].records_out == 3
    assert result.metrics["snk_b"].records_out == 2


def test_join_multi_input_end_to_end(tmp_path) -> None:  # type: ignore[no-untyped-def]
    left, right = tmp_path / "l.csv", tmp_path / "r.csv"
    left.write_text("id,v\n1,a\n2,b\n", encoding="utf-8")
    right.write_text("id,w\n2,x\n3,y\n", encoding="utf-8")
    out = str(tmp_path / "j.json")
    payload = _doc(
        [
            {"id": "a", "type": "source", "plugin": "csv", "config": {"path": str(left)}},
            {"id": "b", "type": "source", "plugin": "csv", "config": {"path": str(right)}},
            {
                "id": "j",
                "type": "transform",
                "plugin": "join",
                "config": {"keys": ["id"], "how": "inner"},
            },
            {"id": "s", "type": "sink", "plugin": "json", "config": {"path": out}},
        ],
        [{"from": "a", "to": "j"}, {"from": "b", "to": "j"}, {"from": "j", "to": "s"}],
    )
    result = Executor(validate_pipeline_dict(payload).pipeline).run()
    assert result.metrics["j"].records_out == 1


def test_aggregate_runs_through_executor(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out = str(tmp_path / "agg.json")
    payload = _doc(
        [
            {
                "id": "src",
                "type": "source",
                "plugin": "csv",
                "config": {"path": "examples/data/orders.csv"},
            },
            {
                "id": "agg",
                "type": "transform",
                "plugin": "aggregate",
                "config": {"group_by": ["status"], "operation": "count", "as": "n"},
            },
            {"id": "snk", "type": "sink", "plugin": "json", "config": {"path": out}},
        ],
        [{"from": "src", "to": "agg"}, {"from": "agg", "to": "snk"}],
    )
    result = Executor(validate_pipeline_dict(payload).pipeline).run()
    assert result.metrics["snk"].records_out == 2  # paid + pending groups


class _FlakyState:
    calls = 0
    fail_times = 1


@register_transform("test_flaky")
class _Flaky(BaseTransform):
    name = "test_flaky"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def validate_config(self) -> None:
        pass

    def process(self, batch: Batch) -> Batch:
        _FlakyState.calls += 1
        if _FlakyState.calls <= _FlakyState.fail_times:
            raise RuntimeError("flaky boom")
        return batch


@register_transform("test_boom")
class _Boom(BaseTransform):
    name = "test_boom"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    def validate_config(self) -> None:
        pass

    def process(self, batch: Batch) -> Batch:
        raise RuntimeError("always fails")


def _flaky_doc(fail_times: int, max_attempts: int, backoff: float = 0) -> Any:
    _FlakyState.calls = 0
    _FlakyState.fail_times = fail_times
    return validate_pipeline_dict(
        _doc(
            [
                {
                    "id": "src",
                    "type": "source",
                    "plugin": "csv",
                    "config": {"path": "examples/data/orders.csv"},
                },
                {"id": "fl", "type": "transform", "plugin": "test_flaky", "config": {}},
                {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
            ],
            [{"from": "src", "to": "fl"}, {"from": "fl", "to": "snk"}],
            max_attempts=max_attempts,
            backoff_seconds=backoff,
        )
    ).pipeline


def test_retry_success_counts_attempts() -> None:
    events: list[ExecutionEvent] = []
    result = Executor(_flaky_doc(1, 3), event_callback=events.append).run()
    assert result.metrics["fl"].attempts == 2
    assert result.metrics["fl"].error_count == 1
    assert any(e.type == "retry_attempt" for e in events)
    assert _FlakyState.calls == 2


def test_retry_exhaustion_reports_node_error() -> None:
    events: list[ExecutionEvent] = []
    with pytest.raises(NodeExecutionError) as exc_info:
        Executor(_flaky_doc(99, 2), event_callback=events.append).run()
    assert exc_info.value.node_id == "fl"
    assert exc_info.value.error_type == "RuntimeError"
    assert exc_info.value.attempts == 2
    assert any(e.type == "node_failed" and e.node_id == "fl" for e in events)


def test_max_attempts_one_means_single_try() -> None:
    with pytest.raises(NodeExecutionError):
        Executor(_flaky_doc(99, 1)).run()
    assert _FlakyState.calls == 1  # initial attempt only, no retries


def test_backoff_delay_observed() -> None:
    started = time.monotonic()
    with pytest.raises(NodeExecutionError):
        Executor(_flaky_doc(99, 2, backoff=0.3)).run()
    assert time.monotonic() - started >= 0.3


def test_source_failure_reports_node_id_and_type() -> None:
    payload = _doc(
        [
            {"id": "src", "type": "source", "plugin": "csv", "config": {"path": "missing.csv"}},
            {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
        ],
        [{"from": "src", "to": "snk"}],
    )
    with pytest.raises(NodeExecutionError) as exc_info:
        Executor(validate_pipeline_dict(payload).pipeline).run()
    assert exc_info.value.node_id == "src"
    assert exc_info.value.error_type == "FileNotFoundError"


def test_pre_cancelled_run_not_marked_completed() -> None:
    token = CancellationToken()
    token.cancel()
    events: list[ExecutionEvent] = []
    pipeline = validate_pipeline_dict(_valid_doc()).pipeline
    with pytest.raises(CancellationError):
        Executor(pipeline, event_callback=events.append).run(token)
    assert not any(e.type == "run_finished" for e in events)
    assert any(e.type == "cancelled" for e in events)


def test_cancel_interrupts_retry_wait() -> None:
    token = CancellationToken()
    started = time.monotonic()
    timer = threading.Timer(0.2, token.cancel)
    timer.start()
    try:
        with pytest.raises(CancellationError):
            Executor(_flaky_doc(99, 5, backoff=30)).run(token)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 10  # did not sleep the full 30s backoff


CLOSED: list[str] = []


@register_source("test_closable")
class _Closable(CsvSource):
    name = "test_closable"

    def close(self) -> None:
        CLOSED.append("closed")
        super().close()


def test_resources_closed_on_failure() -> None:
    CLOSED.clear()
    payload = _doc(
        [
            {
                "id": "src",
                "type": "source",
                "plugin": "test_closable",
                "config": {"path": "examples/data/orders.csv"},
            },
            {"id": "bad", "type": "transform", "plugin": "test_boom", "config": {}},
            {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
        ],
        [{"from": "src", "to": "bad"}, {"from": "bad", "to": "snk"}],
    )
    with pytest.raises(NodeExecutionError):
        Executor(validate_pipeline_dict(payload).pipeline).run()
    assert CLOSED == ["closed"]


def test_concurrent_runs_do_not_share_state() -> None:
    payload = validate_pipeline_dict(
        _doc(
            [
                {
                    "id": "src",
                    "type": "source",
                    "plugin": "csv",
                    "config": {"path": "examples/data/orders.csv"},
                },
                {
                    "id": "dd",
                    "type": "transform",
                    "plugin": "deduplicate",
                    "config": {"keys": ["transaction_id"]},
                },
                {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
            ],
            [{"from": "src", "to": "dd"}, {"from": "dd", "to": "snk"}],
        )
    ).pipeline
    results = []
    errors = []

    def _run() -> None:
        try:
            results.append(Executor(payload).run().metrics["dd"].records_out)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert results == [5, 5, 5]


def test_unsafe_filter_rejected_at_validation() -> None:
    payload = _valid_doc()
    payload["pipeline"]["nodes"][1]["config"] = {"expression": "eval('1')"}
    with pytest.raises(PluginConfigError):
        validate_pipeline_dict(payload)


def test_unknown_plugin_lookup_raises_typed_error() -> None:
    from engine.errors import UnknownPluginError
    from engine.registry import lookup

    with pytest.raises(UnknownPluginError, match="Unknown source plugin"):
        lookup("source", "ghost_xyz")
