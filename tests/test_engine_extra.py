"""Gap-closing tests: error branches and secondary behaviors across the engine."""

from __future__ import annotations

import io
from typing import Any

import httpx
import pytest

from engine.errors import NodeExecutionError, PluginConfigError, UnknownPluginError
from engine.executor import Executor, validate_pipeline_dict
from engine.expression import evaluate
from engine.plugins.base import BaseSink, BaseSource, Batch
from engine.plugins.sinks.console_sink import ConsoleSink
from engine.plugins.sinks.csv_sink import CsvSink
from engine.plugins.sinks.json_sink import JsonSink
from engine.plugins.sinks.postgres_sink import PostgresSink
from engine.plugins.sources.csv_source import CsvSource
from engine.plugins.sources.json_source import JsonSource
from engine.plugins.sources.postgres_source import PostgresSource
from engine.plugins.sources.rest_api_source import RestApiSource
from engine.plugins.transforms.aggregate_transform import AggregateTransform
from engine.plugins.transforms.cast_transform import CastTransform
from engine.plugins.transforms.dedup_transform import DeduplicateTransform
from engine.plugins.transforms.filter_transform import FilterTransform
from engine.plugins.transforms.join_transform import JoinTransform
from engine.plugins.transforms.map_transform import MapTransform
from tests.conftest import _valid_doc
from tests.test_engine_sources import _FakeClient, _FakeResponse  # noqa: F401


def _doc(nodes: list[dict[str, Any]], edges: list[dict[str, str]]) -> Any:
    payload = _valid_doc()
    payload["pipeline"]["nodes"] = nodes
    payload["pipeline"]["edges"] = edges
    payload["pipeline"]["retry"] = {"max_attempts": 1, "backoff_seconds": 0}
    return validate_pipeline_dict(payload).pipeline


# -- base-class lifecycle aliases -----------------------------------------
class _MiniSource(BaseSource):
    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {"type": "object"}

    def validate_config(self) -> None:
        pass

    def read(self) -> Any:
        yield [{"a": 1}]
        self._opened = self._opened  # touch state


class _MiniSink(BaseSink):
    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {"type": "object"}

    def validate_config(self) -> None:
        pass

    def write(self, batch: Batch) -> int:
        return len(batch)


def test_base_aliases_and_open_state() -> None:
    src = _MiniSource({})
    assert not src.is_open
    src.open()
    assert src.is_open
    assert list(src.process()) == [[{"a": 1}]]
    src.close()
    assert not src.is_open
    sink = _MiniSink({})
    sink.open()
    assert sink.process([{"a": 1}]) == 1
    sink.close()


# -- registry wrappers ------------------------------------------------------
def test_registry_lookup_wrappers_and_duplicate() -> None:
    from engine import registry

    assert registry.lookup_source("csv") is CsvSource
    assert registry.lookup_transform("filter") is FilterTransform
    assert registry.lookup_sink("console") is ConsoleSink
    with pytest.raises(ValueError, match="already registered"):
        registry.register_source("csv")(CsvSource)
    with pytest.raises(UnknownPluginError):
        registry.lookup("sink", "ghost")


# -- expression extras -------------------------------------------------------
def test_expression_div_mod_and_membership_fallback() -> None:
    assert evaluate("amount % 2 == 0", {"amount": 4}) is True
    assert evaluate("amount / 2 == 2", {"amount": 4}) is True
    assert evaluate("1 in 5", {"a": 1}) is False  # TypeError -> no match
    assert evaluate("amount > 1.5", {"amount": "2"}) is True


def test_filter_skips_unevaluable_records() -> None:
    plugin = FilterTransform({"expression": "amount / 0 > 1"})
    assert plugin.process([{"amount": 1}]) == []


# -- map extras ---------------------------------------------------------------
def test_map_drop_and_collision_rejected() -> None:
    plugin = MapTransform({"mapping": {"a": "x"}, "drop": ["b"]})
    assert plugin.process([{"a": 1, "b": 2}]) == [{"x": 1}]
    with pytest.raises(PluginConfigError):
        MapTransform({"mapping": "nope"}).validate_config()
    with pytest.raises(RuntimeError, match="same output name"):
        MapTransform({"mapping": {"a": "x", "b": "x"}}).process([{"a": 1, "b": 2}])


# -- cast extras ---------------------------------------------------------------
def test_cast_bool_coercions() -> None:
    plugin = CastTransform({"fields": {"b": "bool"}})
    rows = plugin.process([{"b": 1}, {"b": 0}, {"b": "0"}, {"b": None}, {"b": ""}])
    assert [r["b"] for r in rows] == [True, False, False, None, None]
    with pytest.raises(ValueError, match="Cannot cast"):
        CastTransform({"fields": {"b": "bool"}}).process([{"b": "maybe"}])


def test_cast_int_from_float_string() -> None:
    assert CastTransform({"fields": {"n": "int"}}).process([{"n": "3.7"}]) == [{"n": 3}]


# -- aggregate extras ------------------------------------------------------------
def test_aggregate_min_max_global() -> None:
    plugin = AggregateTransform(
        {
            "group_by": [],
            "aggregations": [
                {"field": "v", "op": "min", "as": "lo"},
                {"field": "v", "op": "max", "as": "hi"},
            ],
        }
    )
    plugin.validate_config()
    plugin.process([{"v": 2}, {"v": 9}])
    assert plugin.finish() == [{"lo": 2, "hi": 9}]


def test_aggregate_empty_input() -> None:
    plugin = AggregateTransform({"group_by": ["c"], "operation": "sum", "field": "v", "as": "t"})
    assert plugin.process([]) == []
    assert plugin.finish() == []


# -- join extras ------------------------------------------------------------------
def test_join_validation_branches() -> None:
    with pytest.raises(PluginConfigError):
        JoinTransform({"keys": ["id"], "how": "sideways"}).validate_config()
    with pytest.raises(PluginConfigError):
        JoinTransform({"keys": "id"}).validate_config()
    with pytest.raises(PluginConfigError):
        JoinTransform({}).validate_config()
    with pytest.raises(PluginConfigError):
        JoinTransform({"keys": ["id"]}).process([{"id": 1}])


def test_join_right_prefix_collision() -> None:
    out = JoinTransform({"keys": ["id"]}).process_many(
        {"l": [{"id": 1, "v": "a"}], "r": [{"id": 1, "v": "b"}]}
    )
    assert out == [{"id": 1, "v": "a", "right_v": "b"}]


def test_dedup_bad_keys_rejected() -> None:
    with pytest.raises(PluginConfigError):
        DeduplicateTransform({"keys": "id"}).validate_config()


# -- source extras ------------------------------------------------------------------
def test_csv_empty_file_yields_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "empty.csv"
    target.write_text("", encoding="utf-8")
    assert list(CsvSource({"path": str(target)}).read()) == []


def test_csv_short_row_normalized(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "short.csv"
    target.write_text("a,b\n1\n", encoding="utf-8")
    batches = list(CsvSource({"path": str(target)}).read())
    assert batches[0][0]["a"] == "1"


def test_json_missing_and_invalid_array(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(FileNotFoundError):
        list(JsonSource({"path": str(tmp_path / "nope.json")}).read())
    bad = tmp_path / "bad.json"
    bad.write_text("[1,", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON array"):
        list(JsonSource({"path": str(bad)}).read())
    with pytest.raises(PluginConfigError):
        JsonSource({}).validate_config()


def test_postgres_source_validation_and_connection_errors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PluginConfigError):
        PostgresSource({"query": "SELECT 1"}).validate_config()
    with pytest.raises(PluginConfigError):
        PostgresSource({"query": "SELECT 1", "dsn": "x", "batch_size": 0}).validate_config()
    # Unreachable postgres DSN -> clear connection error (no server needed).
    src = PostgresSource(
        {"query": "SELECT 1", "dsn": "postgresql://u:p@127.0.0.1:1/db?connect_timeout=1"}
    )
    with pytest.raises(ConnectionError, match="connection failed"):
        list(src.read())


def test_rest_api_error_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    _FakeClient.pages = {}
    _FakeClient.fail_with = None
    with pytest.raises(PluginConfigError):
        RestApiSource({"url": "https://x", "method": "PUT"}).validate_config()
    with pytest.raises(PluginConfigError):
        RestApiSource({"url": "https://x", "max_pages": 0}).validate_config()

    class _ErrClient(_FakeClient):
        def get(self, *a: Any, **k: Any) -> _FakeResponse:
            return _FakeResponse({"e": 1}, status_code=500)

    monkeypatch.setattr(httpx, "Client", _ErrClient)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        list(RestApiSource({"url": "https://x"}).read())

    class _TextClient(_FakeClient):
        def get(self, *a: Any, **k: Any) -> Any:
            class _R:
                status_code = 200
                text = "not json"

                def json(self) -> Any:
                    raise ValueError("nope")

            return _R()

    monkeypatch.setattr(httpx, "Client", _TextClient)
    with pytest.raises(ValueError, match="non-JSON"):
        list(RestApiSource({"url": "https://x"}).read())

    class _PostClient(_FakeClient):
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def post(self, *a: Any, **k: Any) -> _FakeResponse:
            return _FakeResponse([{"ok": True}])

    monkeypatch.setattr(httpx, "Client", _PostClient)
    out = [r for b in RestApiSource({"url": "https://x", "method": "POST"}).read() for r in b]
    assert out == [{"ok": True}]

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    _FakeClient.pages = {1: [{"a": 1}, {"b": 2}]}
    bounded = [
        r
        for b in RestApiSource(
            {
                "url": "https://x",
                "pagination": {"type": "page", "page_size": 10},
                "max_pages": 3,
                "max_records": 1,
            }
        ).read()
        for r in b
    ]
    assert bounded == [{"a": 1}]
    _FakeClient.pages = {1: [{"a": 1}]}
    with pytest.raises(PluginConfigError, match="pagination"):
        list(RestApiSource({"url": "https://x", "pagination": {"type": "cursor"}}).read())
    with pytest.raises(ValueError, match="did not resolve"):
        list(RestApiSource({"url": "https://x", "records_path": "a.b"}).read())


# -- sink extras ----------------------------------------------------------------------
def test_sink_validation_and_closed_branches(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PluginConfigError):
        CsvSink({}).validate_config()
    with pytest.raises(PluginConfigError):
        CsvSink({"path": "x", "mode": "sideways"}).validate_config()
    with pytest.raises(RuntimeError, match="not open"):
        CsvSink({"path": str(tmp_path / "o.csv")}).write([{"a": 1}])
    with pytest.raises(PluginConfigError):
        JsonSink({"path": "x", "mode": "sideways"}).validate_config()
    with pytest.raises(RuntimeError, match="not open"):
        JsonSink({"path": str(tmp_path / "o.json")}).write([{"a": 1}])
    with pytest.raises(PluginConfigError):
        ConsoleSink({"prefix": 5}).validate_config()  # type: ignore[dict-item]
    with pytest.raises(RuntimeError, match="not open"):
        ConsoleSink({}).write([{"a": 1}])
    # Appending onto a corrupt JSON file falls back to a fresh array.
    target = tmp_path / "c.json"
    target.write_text("garbage", encoding="utf-8")
    sink = JsonSink({"path": str(target), "mode": "append"})
    sink.open()
    sink.write([{"a": 1}])
    sink.close()
    assert sink._records == [{"a": 1}]


def test_postgres_sink_error_branches(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    import io as _io

    _ = _io
    monkeypatch.delenv("MISSING_SINK_DSN_XYZ", raising=False)
    sink = PostgresSink({"table": "t", "dsn_env": "MISSING_SINK_DSN_XYZ"})
    with pytest.raises(PluginConfigError):
        sink.open()
    with pytest.raises(RuntimeError, match="not open"):
        PostgresSink({"table": "t", "dsn": "sqlite://x.db"}).write([{"a": 1}])
    bad = PostgresSink({"table": "t", "dsn": f"sqlite:///{tmp_path}/o.db"})
    bad.open()
    with pytest.raises(ValueError, match="invalid column"):
        bad.write([{"bad col!": 1}])
    bad.close()
    bad.close()  # idempotent
    unreachable = PostgresSink(
        {"table": "t", "dsn": "postgresql://u:p@127.0.0.1:1/db?connect_timeout=1"}
    )
    with pytest.raises(ConnectionError, match="connection failed"):
        unreachable.open()


# -- executor extras ---------------------------------------------------------------------
def test_node_execution_error_not_retried() -> None:
    from engine.errors import NodeExecutionError as _NEE
    from engine.plugins.base import BaseTransform as _BT
    from engine.registry import register_transform as _rt

    @_rt("test_direct_nee")
    class _Direct(_BT):
        name = "test_direct_nee"

        @classmethod
        def config_schema(cls) -> dict[str, Any]:
            return {"type": "object"}

        def validate_config(self) -> None:
            pass

        def process(self, batch: Batch) -> Batch:
            raise _NEE("n2", "direct", error_type="Direct", attempts=9)

    pipeline = _doc(
        [
            {
                "id": "n1",
                "type": "source",
                "plugin": "csv",
                "config": {"path": "examples/data/orders.csv"},
            },
            {"id": "n2", "type": "transform", "plugin": "test_direct_nee", "config": {}},
            {"id": "n3", "type": "sink", "plugin": "console", "config": {}},
        ],
        [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3"}],
    )
    with pytest.raises(NodeExecutionError) as exc_info:
        Executor(pipeline, batch_size=2).run()
    assert exc_info.value.error_type == "Direct"  # propagated, not wrapped/retried


def test_sink_without_predecessors_writes_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out = str(tmp_path / "o.csv")
    pipeline = _doc(
        [
            {
                "id": "s",
                "type": "source",
                "plugin": "csv",
                "config": {"path": "examples/data/orders.csv"},
            },
            {
                "id": "lonely",
                "type": "sink",
                "plugin": "csv",
                "config": {"path": out, "mode": "overwrite"},
            },
        ],
        [],
    )
    result = Executor(pipeline).run()
    assert result.metrics["lonely"].records_in == 0


def test_csv_sink_nested_parent_created(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out = str(tmp_path / "deep" / "nested" / "o.csv")
    pipeline = _doc(
        [
            {
                "id": "s",
                "type": "source",
                "plugin": "csv",
                "config": {"path": "examples/data/orders.csv"},
            },
            {"id": "w", "type": "sink", "plugin": "csv", "config": {"path": out}},
        ],
        [{"from": "s", "to": "w"}],
    )
    Executor(pipeline).run()
    assert __import__("pathlib").Path(out).is_file()


def test_console_sink_stream_output() -> None:
    stream = io.StringIO()
    sink = ConsoleSink({}, stream=stream)
    sink.open()
    sink.write([{"a": 1}])
    sink.close()
    assert "{'a': 1}" in stream.getvalue()


def test_cli_malformed_json_and_stdout_schema(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    from engine.__main__ import main

    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert main(["run", str(bad)]) == 1
    assert main(["generate-schema"]) == 0
    assert "pipeline" in capsys.readouterr().out
