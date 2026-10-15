"""Source plugin tests (CSV, JSON, REST with mocks, Postgres-over-sqlite)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import httpx
import pytest

from engine.plugins.sinks import console_sink  # noqa: F401  (ensure registration side effects)
from engine.plugins.sources.csv_source import CsvSource
from engine.plugins.sources.json_source import JsonSource
from engine.plugins.sources.postgres_source import PostgresSource
from engine.plugins.sources.rest_api_source import RestApiSource


def test_csv_source_reads_rows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    target = tmp_path / "in.csv"
    target.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    src = CsvSource({"path": str(target), "batch_size": 1})
    src.validate_config()
    batches = list(src.read())
    assert len(batches) == 2
    assert batches[0] == [{"a": "1", "b": "2"}]


def test_csv_source_missing_file() -> None:
    src = CsvSource({"path": "does-not-exist.csv"})
    src.validate_config()
    with pytest.raises(FileNotFoundError):
        list(src.read())


def test_csv_source_bad_config() -> None:
    from engine.errors import PluginConfigError

    with pytest.raises(PluginConfigError):
        CsvSource({}).validate_config()
    with pytest.raises(PluginConfigError):
        CsvSource({"path": "x.csv", "delimiter": ";;"}).validate_config()


def test_json_source_lines_and_array(tmp_path) -> None:  # type: ignore[no-untyped-def]
    lines = tmp_path / "a.jsonl"
    lines.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
    assert [r for b in JsonSource({"path": str(lines)}).read() for r in b] == [{"a": 1}, {"a": 2}]
    arr = tmp_path / "b.json"
    arr.write_text('[{"a": 1}, {"a": 2}]', encoding="utf-8")
    assert [r for b in JsonSource({"path": str(arr)}).read() for r in b] == [{"a": 1}, {"a": 2}]


def test_json_source_malformed_line(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"a": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        list(JsonSource({"path": str(bad)}).read())


def test_json_source_non_object_array(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="not an object"):
        list(JsonSource({"path": str(bad)}).read())


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """Minimal httpx.Client stand-in supporting GET pagination."""

    pages: dict[int, list[dict[str, Any]]] = {}
    fail_with: Exception | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *args: Any) -> None:
        _ = args

    def get(
        self, url: str, params: dict[str, Any] | None = None, timeout: float = 10
    ) -> _FakeResponse:
        _ = url, timeout
        if _FakeClient.fail_with is not None:
            raise _FakeClient.fail_with
        page = int((params or {}).get("page", 1))
        return _FakeResponse({"items": list(_FakeClient.pages.get(page, []))})

    def post(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        _ = args, kwargs
        raise AssertionError("unexpected POST")


@pytest.fixture()
def _fake_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    _FakeClient.pages = {}
    _FakeClient.fail_with = None


def test_rest_api_single_page(_fake_http: None) -> None:
    _FakeClient.pages = {1: [{"a": 1}]}
    src = RestApiSource({"url": "https://api.example.com/data"})
    src.validate_config()
    assert [r for b in src.read() for r in b] == [{"a": 1}]


def test_rest_api_pagination_bounded(_fake_http: None) -> None:
    _FakeClient.pages = {1: [{"a": 1}], 2: [{"a": 2}], 3: []}
    src = RestApiSource(
        {
            "url": "https://api.example.com/data",
            "pagination": {"type": "page", "page_size": 1},
            "max_pages": 5,
        }
    )
    records = [r for b in src.read() for r in b]
    assert records == [{"a": 1}, {"a": 2}]


def test_rest_api_timeout_surfaced(_fake_http: None) -> None:
    _FakeClient.fail_with = httpx.TimeoutException("slow")
    src = RestApiSource({"url": "https://api.example.com/data"})
    with pytest.raises(TimeoutError):
        list(src.read())


def test_rest_api_bad_url_rejected() -> None:
    from engine.errors import PluginConfigError

    with pytest.raises(PluginConfigError):
        RestApiSource({"url": "ftp://x"}).validate_config()


def test_postgres_source_over_sqlite(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "src.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (a TEXT, b TEXT)")
    conn.executemany("INSERT INTO t VALUES (?, ?)", [("1", "x"), ("2", "y")])
    conn.commit()
    conn.close()
    monkeypatch.setenv("TEST_SRC_DSN", f"sqlite:///{db_path}")
    src = PostgresSource({"query": "SELECT a, b FROM t", "dsn_env": "TEST_SRC_DSN"})
    src.validate_config()
    assert [r for b in src.read() for r in b] == [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]


def test_postgres_source_missing_dsn_env(monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from engine.errors import PluginConfigError

    monkeypatch.delenv("MISSING_DSN_XYZ", raising=False)
    src = PostgresSource({"query": "SELECT 1", "dsn_env": "MISSING_DSN_XYZ"})
    with pytest.raises(PluginConfigError):
        list(src.read())


def test_postgres_source_bad_query(tmp_path) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()
    src = PostgresSource({"query": "SELECT * FROM nope", "dsn": f"sqlite:///{db_path}"})
    with pytest.raises(RuntimeError, match="query failed"):
        list(src.read())
