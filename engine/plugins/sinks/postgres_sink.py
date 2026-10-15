"""PostgreSQL sink with append / overwrite / upsert modes.

Guarantees & limitations (documented honestly):
- Writes run inside explicit transactions (commit per batch, rollback on
  error), so a failed batch does not leave partial rows.
- ``append`` is at-least-once under retries: a retry after a commit that
  failed to acknowledge may duplicate rows. Use ``upsert`` with
  ``conflict_keys`` for idempotent re-execution.
- ``upsert`` uses ``INSERT ... ON CONFLICT (keys) DO UPDATE`` on PostgreSQL.
  On SQLite (dev/test DSNs) it emulates with ``INSERT OR REPLACE``.
- ``overwrite`` deletes all rows in the target table first (in the same
  transaction as the first batch). It is NOT atomic across concurrent runs.
- Exactly-once is NOT claimed.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
from typing import Any

from engine.errors import PluginConfigError
from engine.plugins.base import BaseSink, Batch
from engine.registry import register_sink

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _split_table(table: str) -> str:
    parts = table.split(".")
    return ".".join(_quote_ident(p) for p in parts)


@register_sink("postgres")
class PostgresSink(BaseSink):
    """Write record batches into a database table."""

    name = "postgres"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._conn: Any = None
        self._is_sqlite = False
        self._columns: list[str] = []
        self._overwrote = False

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["table"],
            "properties": {
                "table": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["append", "overwrite", "upsert"],
                    "default": "append",
                },
                "conflict_keys": {"type": "array", "items": {"type": "string"}},
                "dsn": {"type": "string"},
                "dsn_env": {"type": "string"},
            },
            "additionalProperties": False,
        }

    def validate_config(self) -> None:
        table = self.config.get("table")
        if not table or not isinstance(table, str):
            raise PluginConfigError("?", "postgres sink requires 'table' (string)")
        for part in table.split("."):
            if not _IDENTIFIER.match(part):
                raise PluginConfigError("?", f"postgres sink: invalid table identifier: {table!r}")
        mode = self.config.get("mode", "append")
        if mode not in ("append", "overwrite", "upsert"):
            raise PluginConfigError("?", "postgres sink 'mode' must be append|overwrite|upsert")
        if mode == "upsert":
            keys = self.config.get("conflict_keys")
            if not keys or not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                raise PluginConfigError(
                    "?", "postgres sink upsert mode requires 'conflict_keys' (list of strings)"
                )
            for k in keys:
                if not _IDENTIFIER.match(k):
                    raise PluginConfigError("?", f"postgres sink: invalid conflict key: {k!r}")
        if not self.config.get("dsn") and not self.config.get("dsn_env"):
            raise PluginConfigError("?", "postgres sink requires 'dsn' or 'dsn_env'")

    def _resolve_dsn(self) -> str:
        if self.config.get("dsn_env"):
            value = os.environ.get(str(self.config["dsn_env"]), "")
            if not value:
                raise PluginConfigError(
                    "?", f"Environment variable '{self.config['dsn_env']}' is not set"
                )
            return value
        return str(self.config.get("dsn", ""))

    def open(self) -> None:
        self.validate_config()
        dsn = self._resolve_dsn()
        self._is_sqlite = dsn.startswith("sqlite:") or dsn.endswith(".db") or dsn == ":memory:"
        if self._is_sqlite:
            path = dsn.removeprefix("sqlite:///").removeprefix("sqlite://").removeprefix("sqlite:")
            try:
                self._conn = sqlite3.connect(path or ":memory:")
            except sqlite3.Error as exc:
                raise ConnectionError(f"SQLite sink connection failed: {exc}") from exc
        else:
            try:
                import psycopg
            except ImportError as exc:
                raise ImportError("psycopg is required for postgres sinks") from exc
            try:
                self._conn = psycopg.connect(dsn, connect_timeout=10)
                self._conn.autocommit = False
            except Exception as exc:
                raise ConnectionError(f"Postgres sink connection failed: {exc}") from exc
        self._overwrote = False
        self._opened = True

    def _ensure_table(self, columns: list[str]) -> None:
        assert self._conn is not None
        table = _split_table(str(self.config["table"]))
        col_defs = ", ".join(f"{_quote_ident(c)} TEXT" for c in columns)
        assert all(_IDENTIFIER.match(c) for c in columns), "column names must be identifiers"
        if self._is_sqlite:
            self._conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_defs})")
            existing = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            for col in columns:
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {_quote_ident(col)} TEXT")
        else:
            with self._conn.cursor() as cur:
                cur.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_defs})")
                cur.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                    (str(self.config["table"]).split(".")[-1],),
                )
                existing = {r[0] for r in cur.fetchall()}
                for col in columns:
                    if col not in existing:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN {_quote_ident(col)} TEXT")
        if hasattr(self._conn, "commit"):
            self._conn.commit()
        # Upsert needs a uniqueness target for the conflict keys (both the
        # PostgreSQL ON CONFLICT clause and the SQLite OR REPLACE emulation).
        if str(self.config.get("mode", "append")) == "upsert":
            keys = [k for k in self.config.get("conflict_keys", []) if k in columns]
            if keys:
                index = (
                    "chaosflow_uq_"
                    + re.sub(r"\W+", "_", str(self.config["table"]) + "_" + "_".join(keys))[:50]
                )
                key_list = ", ".join(_quote_ident(k) for k in keys)
                if self._is_sqlite:
                    self._conn.execute(
                        f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table} ({key_list})"
                    )
                    self._conn.commit()
                else:
                    with self._conn.cursor() as cur:
                        cur.execute(
                            f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON {table} ({key_list})"
                        )
                    self._conn.commit()

    def write(self, batch: Batch) -> int:
        if not self._opened or self._conn is None:
            raise RuntimeError("postgres sink is not open (call open() first)")
        if not batch:
            return 0
        mode = str(self.config.get("mode", "append"))
        columns: list[str] = []
        for record in batch:
            for key in record:
                if key not in columns:
                    if not _IDENTIFIER.match(key):
                        raise ValueError(f"postgres sink: invalid column name: {key!r}")
                    columns.append(key)
        self._ensure_table(columns)
        self._columns = columns
        table = _split_table(str(self.config["table"]))
        rows = [[None if r.get(c) is None else str(r.get(c)) for c in columns] for r in batch]
        placeholders = (
            ", ".join(["%s"] * len(columns))
            if not self._is_sqlite
            else ", ".join(["?"] * len(columns))
        )
        col_list = ", ".join(_quote_ident(c) for c in columns)
        try:
            if mode == "overwrite" and not self._overwrote:
                if self._is_sqlite:
                    self._conn.execute(f"DELETE FROM {table}")
                else:
                    with self._conn.cursor() as cur:
                        cur.execute(f"DELETE FROM {table}")
                self._overwrote = True
            if mode == "upsert":
                keys: list[str] = list(self.config["conflict_keys"])
                if self._is_sqlite:
                    sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
                    self._conn.executemany(sql, rows)
                else:
                    non_keys = [c for c in columns if c not in keys]
                    set_clause = ", ".join(
                        f"{_quote_ident(c)} = EXCLUDED.{_quote_ident(c)}" for c in non_keys
                    )
                    if not set_clause:
                        set_clause = ", ".join(
                            f"{_quote_ident(c)} = EXCLUDED.{_quote_ident(c)}" for c in keys
                        )
                    conflict = ", ".join(_quote_ident(k) for k in keys)
                    sql = (
                        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                        f"ON CONFLICT ({conflict}) DO UPDATE SET {set_clause}"
                    )
                    with self._conn.cursor() as cur:
                        cur.executemany(sql, rows)
            else:
                sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
                if self._is_sqlite:
                    self._conn.executemany(sql, rows)
                else:
                    with self._conn.cursor() as cur:
                        cur.executemany(sql, rows)
            self._conn.commit()
        except Exception as exc:
            with contextlib.suppress(Exception):
                self._conn.rollback()
            raise RuntimeError(f"postgres sink write failed (rolled back): {exc}") from exc
        return len(batch)

    def close(self) -> None:
        try:
            if self._conn is not None:
                with contextlib.suppress(Exception):
                    self._conn.close()
        finally:
            self._conn = None
            self._opened = False
