"""Sink plugin tests (CSV, JSON, console, postgres-over-sqlite)."""

from __future__ import annotations

import csv
import io
import json
import sqlite3

import pytest

from engine.errors import PluginConfigError
from engine.plugins.sinks.console_sink import ConsoleSink
from engine.plugins.sinks.csv_sink import CsvSink
from engine.plugins.sinks.json_sink import JsonSink
from engine.plugins.sinks.postgres_sink import PostgresSink


def test_csv_sink_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "out.csv"
    sink = CsvSink({"path": str(target), "mode": "overwrite"})
    sink.open()
    assert sink.write([{"a": "1"}, {"a": "2"}]) == 2
    sink.close()
    with target.open(encoding="utf-8") as fh:
        assert list(csv.DictReader(fh)) == [{"a": "1"}, {"a": "2"}]


def test_csv_sink_append(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "out.csv"
    for _ in range(2):
        sink = CsvSink({"path": str(target), "mode": "append"})
        sink.open()
        sink.write([{"a": "1"}])
        sink.close()
    with target.open(encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == 2


def test_json_sink_overwrite_and_append(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "out.json"
    sink = JsonSink({"path": str(target)})
    sink.open()
    sink.write([{"a": 1}])
    sink.close()
    assert json.loads(target.read_text(encoding="utf-8")) == [{"a": 1}]
    sink2 = JsonSink({"path": str(target), "mode": "append"})
    sink2.open()
    sink2.write([{"a": 2}])
    sink2.close()
    assert json.loads(target.read_text(encoding="utf-8")) == [{"a": 1}, {"a": 2}]


def test_console_sink_captures() -> None:
    stream = io.StringIO()
    sink = ConsoleSink({"prefix": ">> "}, stream=stream)
    sink.open()
    assert sink.write([{"a": 1}]) == 1
    sink.close()
    assert sink.written == [{"a": 1}]
    assert ">> " in stream.getvalue()


def _sqlite_dsn(tmp_path, name: str = "out.db") -> str:  # type: ignore[no-untyped-def]
    return f"sqlite:///{tmp_path / name}"


def test_postgres_sink_append_and_query(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dsn = _sqlite_dsn(tmp_path)
    sink = PostgresSink({"table": "orders_clean", "mode": "append", "dsn": dsn})
    sink.validate_config()
    sink.open()
    assert sink.write([{"id": "1", "v": "a"}, {"id": "2", "v": "b"}]) == 2
    sink.close()
    conn = sqlite3.connect(dsn.removeprefix("sqlite:///"))
    assert conn.execute("SELECT COUNT(*) FROM orders_clean").fetchone()[0] == 2


def test_postgres_sink_overwrite(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dsn = _sqlite_dsn(tmp_path)
    for mode_rows in ([{"id": "1"}], [{"id": "2"}, {"id": "3"}]):
        sink = PostgresSink({"table": "t", "mode": "overwrite", "dsn": dsn})
        sink.open()
        sink.write(mode_rows)
        sink.close()
    conn = sqlite3.connect(dsn.removeprefix("sqlite:///"))
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2


def test_postgres_sink_upsert_idempotent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dsn = _sqlite_dsn(tmp_path)
    cfg = {"table": "t", "mode": "upsert", "conflict_keys": ["id"], "dsn": dsn}
    for val in ("a", "b"):
        sink = PostgresSink(cfg)
        sink.open()
        sink.write([{"id": "1", "v": val}])
        sink.close()
    conn = sqlite3.connect(dsn.removeprefix("sqlite:///"))
    rows = conn.execute("SELECT v FROM t").fetchall()
    assert rows == [("b",)]  # upsert replaced rather than duplicated


def test_postgres_sink_validation() -> None:
    with pytest.raises(PluginConfigError):
        PostgresSink({"table": "bad-table!", "dsn": "x"}).validate_config()
    with pytest.raises(PluginConfigError):
        PostgresSink({"table": "t", "mode": "upsert", "dsn": "x"}).validate_config()
    with pytest.raises(PluginConfigError):
        PostgresSink({"table": "t"}).validate_config()
