"""PostgreSQL query-based source.

The DSN is read from ``dsn_env`` (name of an environment variable) or from
``dsn`` directly. ``dsn_env`` is preferred so secrets never live in the
pipeline JSON. For local development and tests, ``sqlite://``/``sqlite:``
DSNs (or a plain file path ending in ``.db``) are also accepted via the
stdlib ``sqlite3`` driver; anything else uses ``psycopg``.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseSource, Batch
from engine.registry import register_source


@register_source("postgres")
class PostgresSource(BaseSource):
    """Run a read query and stream rows as records."""

    name = "postgres"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "dsn": {"type": "string"},
                "dsn_env": {"type": "string"},
                "batch_size": {"type": "integer", "minimum": 1, "default": 500},
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        query = self.config.get("query")
        if not query or not isinstance(query, str):
            raise PluginConfigError("?", "postgres source requires 'query' (string)")
        if not self.config.get("dsn") and not self.config.get("dsn_env"):
            raise PluginConfigError("?", "postgres source requires 'dsn' or 'dsn_env'")
        batch_size = self.config.get("batch_size", 500)
        if not isinstance(batch_size, int) or batch_size < 1:
            raise PluginConfigError("?", "postgres 'batch_size' must be a positive integer")

    def _resolve_dsn(self) -> str:
        if self.config.get("dsn_env"):
            value = os.environ.get(str(self.config["dsn_env"]), "")
            if not value:
                raise PluginConfigError(
                    "?", f"Environment variable '{self.config['dsn_env']}' is not set"
                )
            return value
        return str(self.config.get("dsn", ""))

    def read(self) -> Iterator[Batch]:
        dsn = self._resolve_dsn()
        query = str(self.config["query"])
        batch_size = int(self.config.get("batch_size", 500))
        if dsn.startswith("sqlite:") or dsn.endswith(".db"):
            yield from self._read_sqlite(dsn, query, batch_size)
        else:
            yield from self._read_postgres(dsn, query, batch_size)

    def _read_sqlite(self, dsn: str, query: str, batch_size: int) -> Iterator[Batch]:
        path = dsn.removeprefix("sqlite:///").removeprefix("sqlite://").removeprefix("sqlite:")
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            raise ConnectionError(f"SQLite source connection failed: {exc}") from exc
        try:
            try:
                cursor = conn.execute(query)
            except sqlite3.Error as exc:
                raise RuntimeError(f"Postgres/sqlite source query failed: {exc}") from exc
            columns = [d[0] for d in (cursor.description or [])]
            batch: Batch = []
            for row in cursor:
                batch.append({col: row[col] for col in columns})
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
            if batch:
                yield batch
        finally:
            conn.close()

    def _read_postgres(self, dsn: str, query: str, batch_size: int) -> Iterator[Batch]:
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError(
                "psycopg is required for postgres sources (pip install psycopg[binary])"
            ) from exc
        try:
            conn = psycopg.connect(dsn, connect_timeout=10)
        except Exception as exc:
            raise ConnectionError(f"Postgres source connection failed: {exc}") from exc
        try:
            with conn, conn.cursor() as cur:
                try:
                    cur.execute(query)
                except Exception as exc:
                    raise RuntimeError(f"Postgres source query failed: {exc}") from exc
                columns = [d[0] for d in (cur.description or [])]
                batch = []
                for row in cur:
                    batch.append(dict(zip(columns, row, strict=False)))
                    if len(batch) >= batch_size:
                        yield batch
                        batch = []
                if batch:
                    yield batch
        finally:
            conn.close()
