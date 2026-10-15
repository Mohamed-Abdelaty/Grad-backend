"""WebSocket, plugin catalog, health, and error-format tests + full end-to-end."""

from __future__ import annotations

import csv
import uuid

from fastapi.testclient import TestClient

from tests.conftest import _valid_doc, create_pipeline, make_csv, wait_for_status


def test_websocket_streams_live_events_and_done(
    client: TestClient, user_headers: dict, tmp_path
) -> None:
    src = make_csv(tmp_path / "in.csv", rows=20000)
    out = str(tmp_path / "out.csv")
    doc = _valid_doc()
    doc["pipeline"]["nodes"][0] = {
        "id": "src_1",
        "type": "source",
        "plugin": "csv",
        "config": {"path": src, "batch_size": 500},
    }
    doc["pipeline"]["nodes"][2] = {
        "id": "snk_1",
        "type": "sink",
        "plugin": "csv",
        "config": {"path": out, "mode": "overwrite"},
    }
    doc["pipeline"]["retry"] = {"max_attempts": 1, "backoff_seconds": 0}
    pipe = create_pipeline(client, user_headers, doc)
    authz = {"Authorization": user_headers["Authorization"]}
    run_id = client.post(f"/pipelines/{pipe['id']}/runs", headers=authz).json()["id"]
    token = user_headers["_access"]
    seen = {"event": 0, "progress": 0, "history": False, "done": None}
    with client.websocket_connect(f"/ws/runs/{run_id}?token={token}") as ws:
        for _ in range(500):
            msg = ws.receive_json()
            if msg["type"] == "history":
                seen["history"] = True
            elif msg["type"] == "event":
                seen["event"] += 1
            elif msg["type"] == "progress":
                seen["progress"] += 1
                assert "note" in msg  # estimate is explicitly distinguished
            elif msg["type"] == "done":
                seen["done"] = msg["status"]
                break
    assert seen["history"] is True
    assert seen["done"] == "succeeded"
    assert seen["event"] > 0 and seen["progress"] > 0


def test_websocket_reconnect_replays_history(
    client: TestClient, user_headers: dict, tmp_path
) -> None:
    src = make_csv(tmp_path / "in.csv", rows=5)
    out = str(tmp_path / "out.csv")
    doc = _valid_doc()
    doc["pipeline"]["nodes"][0]["config"] = {"path": src}
    doc["pipeline"]["nodes"][2] = {
        "id": "snk_1",
        "type": "sink",
        "plugin": "csv",
        "config": {"path": out, "mode": "overwrite"},
    }
    pipe = create_pipeline(client, user_headers, doc)
    authz = {"Authorization": user_headers["Authorization"]}
    run_id = client.post(f"/pipelines/{pipe['id']}/runs", headers=authz).json()["id"]
    wait_for_status(client, user_headers, run_id, {"succeeded"})
    token = user_headers["_access"]
    with client.websocket_connect(f"/ws/runs/{run_id}?token={token}") as ws:
        first = ws.receive_json()
        assert first["type"] == "history"
        assert len(first["logs"]) > 0
        second = ws.receive_json()
        assert second == {"type": "done", "run_id": run_id, "status": "succeeded"}


def test_websocket_rejects_bad_token_and_foreign_run(
    client: TestClient, user_headers: dict, tmp_path
) -> None:
    import starlette.websockets  # noqa: F401

    src = make_csv(tmp_path / "in.csv", rows=5)
    doc = _valid_doc()
    doc["pipeline"]["nodes"][0]["config"] = {"path": src}
    pipe = create_pipeline(client, user_headers, doc)
    authz = {"Authorization": user_headers["Authorization"]}
    run_id = client.post(f"/pipelines/{pipe['id']}/runs", headers=authz).json()["id"]
    wait_for_status(client, user_headers, run_id, {"succeeded"})
    try:
        with client.websocket_connect(f"/ws/runs/{run_id}?token=bogus"):
            raise AssertionError("should have been rejected")
    except Exception:
        pass  # close with policy violation is the expected outcome
    email = f"w{uuid.uuid4().hex[:8]}@example.com"
    client.post("/auth/register", json={"email": email, "password": "password123"})
    other = client.post("/auth/login", json={"email": email, "password": "password123"}).json()
    try:
        with client.websocket_connect(f"/ws/runs/{run_id}?token={other['access_token']}"):
            raise AssertionError("foreign run should have been rejected")
    except Exception:
        pass


def test_plugin_catalog_matches_registry(client: TestClient, user_headers: dict) -> None:
    from engine.registry import catalog

    authz = {"Authorization": user_headers["Authorization"]}
    resp = client.get("/plugins", headers=authz)
    assert resp.status_code == 200
    entries = {(e["category"], e["name"]): e["config_schema"] for e in resp.json()}
    expected = {(e["category"], e["name"]): e["config_schema"] for e in catalog()}
    assert entries == expected  # catalog derives from the live registry, no drift
    assert ("source", "csv") in entries and ("transform", "filter") in entries
    assert ("sink", "postgres") in entries and ("source", "rest_api") in entries
    filtered = client.get("/plugins?category=source", headers=authz).json()
    assert filtered and all(e["category"] == "source" for e in filtered)


def test_health_and_error_format(client: TestClient) -> None:
    health = client.get("/health").json()
    assert health["status"] in ("ok", "degraded")
    assert health["version"]
    assert client.get("/api/health").json()["status"] == health["status"]
    missing = client.get(
        "/pipelines/00000000-0000-4000-8000-000000000000", headers={"Authorization": "Bearer x"}
    )
    assert missing.status_code == 401
    body = missing.json()
    assert set(body["error"].keys()) >= {"code", "message"}
    bad = client.post("/auth/register", json={"email": "x", "password": "short"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"


def test_end_to_end_api_to_engine_with_output_verification(
    client: TestClient, user_headers: dict, tmp_path
) -> None:
    """Create pipeline via API -> run real engine -> verify output + progress."""
    src = make_csv(tmp_path / "orders.csv", rows=20)
    out = str(tmp_path / "clean.csv")
    doc = _valid_doc()
    doc["pipeline"]["nodes"] = [
        {"id": "src_1", "type": "source", "plugin": "csv", "config": {"path": src}},
        {
            "id": "tr_1",
            "type": "transform",
            "plugin": "cast",
            "config": {"fields": {"amount": "int"}},
        },
        {
            "id": "tr_2",
            "type": "transform",
            "plugin": "filter",
            "config": {"expression": "amount > 15"},
        },
        {
            "id": "tr_3",
            "type": "transform",
            "plugin": "map",
            "config": {"mapping": {"transaction_id": "id"}},
        },
        {
            "id": "snk_1",
            "type": "sink",
            "plugin": "csv",
            "config": {"path": out, "mode": "overwrite"},
        },
    ]
    doc["pipeline"]["edges"] = [
        {"from": "src_1", "to": "tr_1"},
        {"from": "tr_1", "to": "tr_2"},
        {"from": "tr_2", "to": "tr_3"},
        {"from": "tr_3", "to": "snk_1"},
    ]
    doc["pipeline"]["retry"] = {"max_attempts": 2, "backoff_seconds": 0}
    pipe = create_pipeline(client, user_headers, doc, name="e2e")
    authz = {"Authorization": user_headers["Authorization"]}
    run_id = client.post(f"/pipelines/{pipe['id']}/runs", headers=authz).json()["id"]
    final = wait_for_status(client, user_headers, run_id, {"succeeded"})
    # amount = 10+i for i in 0..19 -> amount > 15 keeps i >= 6 -> 14 records.
    assert final["records_out"] == 14
    assert final["metrics"]["snk_1"]["records_out"] == 14
    with open(out, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 14
    assert all(int(r["amount"]) > 15 for r in rows)
    assert "id" in rows[0] and "transaction_id" not in rows[0]
    versions = client.get(f"/pipelines/{pipe['id']}/versions", headers=authz).json()
    assert versions["total"] == 1
    token = user_headers["_access"]
    with client.websocket_connect(f"/ws/runs/{run_id}?token={token}") as ws:
        history = ws.receive_json()
        assert history["type"] == "history" and len(history["logs"]) > 0
