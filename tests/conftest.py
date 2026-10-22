"""Shared pytest fixtures: isolated file DB, API client, auth helpers, pipeline builders."""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from typing import Any

# Set test database URL BEFORE any app imports
os.environ["DATABASE_URL"] = "sqlite:///./.test_chaosflow.db"

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command
from app.db.session import SessionLocal
from app.models.entities import (
    ApiKey,
    AuditEvent,
    Notification,
    NotificationDelivery,
    NotificationPreference,
    Pipeline,
    PipelineVersion,
    RefreshToken,
    Run,
    RunLog,
    Schedule,
    Template,
    User,
)
from app.services import run_worker


def _run_migrations():
    """Run alembic migrations on test database."""
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", "sqlite:///./.test_chaosflow.db")
    command.upgrade(alembic_cfg, "head")


def _valid_doc(**overrides: Any) -> dict[str, Any]:
    """Minimal valid pipeline document (csv -> filter -> console)."""
    doc: dict[str, Any] = {
        "pipeline": {
            "id": str(uuid.uuid4()),
            "name": "test-pipe",
            "version": 1,
            "schedule": None,
            "nodes": [
                {
                    "id": "src_1",
                    "type": "source",
                    "plugin": "csv",
                    "config": {"path": "examples/data/orders.csv"},
                },
                {
                    "id": "tr_1",
                    "type": "transform",
                    "plugin": "filter",
                    "config": {"expression": "amount > 100"},
                },
                {"id": "snk_1", "type": "sink", "plugin": "console", "config": {}},
            ],
            "edges": [{"from": "src_1", "to": "tr_1"}, {"from": "tr_1", "to": "snk_1"}],
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
        }
    }
    doc["pipeline"].update(overrides)
    return doc


@pytest.fixture(scope="session", autouse=True)
def _setup_db() -> Any:
    """Create tables once for the isolated test database file."""

    # Remove existing test DB
    with contextlib.suppress(OSError):
        os.remove("./.test_chaosflow.db")

    # Run migrations
    _run_migrations()
    yield
    with contextlib.suppress(OSError):
        os.remove("./.test_chaosflow.db")


@pytest.fixture(scope="session")
def client() -> TestClient:
    """API test client sharing the isolated test database file."""
    from app.main import create_app

    app = create_app()
    with TestClient(app) as tc:
        yield tc  # type: ignore[misc]


@pytest.fixture(autouse=True)
def _clean_db():
    """Wipe all rows between tests for isolation; reset worker state."""
    yield
    db = SessionLocal()
    try:
        # Phase 2 tables must be deleted before Phase 1 tables due to FK constraints.
        for model in (
            NotificationDelivery,
            Notification,
            NotificationPreference,
            AuditEvent,
            ApiKey,
            Template,
            Schedule,
            RunLog,
            Run,
            PipelineVersion,
            Pipeline,
            RefreshToken,
            User,
        ):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()
    with run_worker._lock:
        run_worker._tokens.clear()
    with run_worker._sub_lock:
        run_worker._subscribers.clear()


@pytest.fixture()
def user_headers(client: TestClient) -> dict[str, str]:
    """Register + login a fresh user; return Authorization headers + tokens."""
    email = f"u{uuid.uuid4().hex[:8]}@example.com"
    assert (
        client.post("/auth/register", json={"email": email, "password": "password123"}).status_code
        == 201
    )
    resp = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert resp.status_code == 200
    tokens = resp.json()
    return {
        "Authorization": f"Bearer {tokens['access_token']}",
        "_refresh": tokens["refresh_token"],
        "_access": tokens["access_token"],
        "_email": email,
    }


def make_csv(path: Any, rows: int = 5, start: int = 0) -> str:
    """Write a deterministic CSV file; return its string path."""
    path = str(path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("transaction_id,customer_id,amount,status\n")
        for i in range(start, start + rows):
            fh.write(f"t{i},c{i % 3},{10 + i},paid\n")
    return path


def create_pipeline(
    client: TestClient, headers: dict[str, str], doc: dict[str, Any], name: str = "p"
) -> dict[str, Any]:
    """Create a pipeline through the API; return the detail body."""
    resp = client.post(
        "/pipelines",
        json={"name": name, "config": doc},
        headers={"Authorization": headers["Authorization"]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def wait_for_status(
    client: TestClient,
    headers: dict[str, str],
    run_id: str,
    statuses: set[str],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Poll a run until its status is in ``statuses`` (or raise on timeout)."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    authz = {"Authorization": headers["Authorization"]}
    while time.monotonic() < deadline:
        resp = client.get(f"/runs/{run_id}", headers=authz)
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] in statuses:
            return last
        time.sleep(0.1)
    raise TimeoutError(f"Run {run_id} did not reach {statuses}; last={last}")
