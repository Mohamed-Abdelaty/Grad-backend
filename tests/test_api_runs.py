"""Run lifecycle tests: background start, status, stop, history, ownership."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import _valid_doc, create_pipeline, make_csv, wait_for_status


def _csv_pipeline(path: str, out: str, expression: str = "amount > 15") -> dict:
    doc = _valid_doc()
    doc["pipeline"]["nodes"][0] = {
        "id": "src_1",
        "type": "source",
        "plugin": "csv",
        "config": {"path": path},
    }
    doc["pipeline"]["nodes"][1] = {
        "id": "tr_1",
        "type": "transform",
        "plugin": "filter",
        "config": {"expression": expression},
    }
    doc["pipeline"]["nodes"][2] = {
        "id": "snk_1",
        "type": "sink",
        "plugin": "csv",
        "config": {"path": out, "mode": "overwrite"},
    }
    doc["pipeline"]["retry"] = {"max_attempts": 1, "backoff_seconds": 0}
    return doc


def test_run_executes_in_background_and_succeeds(
    client: TestClient, user_headers: dict, tmp_path
) -> None:
    src = make_csv(tmp_path / "in.csv", rows=50)
    out = str(tmp_path / "out.csv")
    pipe = create_pipeline(client, user_headers, _csv_pipeline(src, out))
    authz = {"Authorization": user_headers["Authorization"]}
    started = client.post(f"/pipelines/{pipe['id']}/runs", headers=authz)
    assert started.status_code == 202
    run_id = started.json()["id"]
    # Returns promptly: initial status is queued/running, never blocks to completion.
    assert started.json()["status"] in ("queued", "running")
    final = wait_for_status(client, user_headers, run_id, {"succeeded"})
    assert final["progress"] == 100.0
    assert final["records_out"] > 0
    assert final["metrics"]["tr_1"]["records_out"] == final["records_out"]
    # Output file was really written by the engine (no fabricated success).
    with open(out, encoding="utf-8") as fh:
        assert len(fh.readlines()) > 1
    logs = client.get(f"/runs/{run_id}/logs", headers=authz).json()
    assert logs["total"] > 0
    assert any(item["event"] == "node_finished" for item in logs["items"])


def test_run_failure_reports_node_and_error(
    client: TestClient, user_headers: dict, tmp_path
) -> None:
    out = str(tmp_path / "out.csv")
    doc = _csv_pipeline(str(tmp_path / "missing.csv"), out)
    pipe = create_pipeline(client, user_headers, doc)
    authz = {"Authorization": user_headers["Authorization"]}
    run_id = client.post(f"/pipelines/{pipe['id']}/runs", headers=authz).json()["id"]
    final = wait_for_status(client, user_headers, run_id, {"failed"})
    assert final["failed_node_id"] == "src_1"
    assert final["error_type"] == "FileNotFoundError"
    assert final["error"]


def test_stop_cancels_running_run(client: TestClient, user_headers: dict, tmp_path) -> None:
    src = make_csv(tmp_path / "big.csv", rows=60000)
    out = str(tmp_path / "big_out.csv")
    pipe = create_pipeline(client, user_headers, _csv_pipeline(src, out))
    authz = {"Authorization": user_headers["Authorization"]}
    run_id = client.post(f"/pipelines/{pipe['id']}/runs", headers=authz).json()["id"]
    wait_for_status(client, user_headers, run_id, {"running"})
    stop = client.post(f"/runs/{run_id}/stop", headers=authz)
    assert stop.status_code == 200
    final = wait_for_status(client, user_headers, run_id, {"cancelled"})
    assert final["status"] == "cancelled"  # never marked completed
    # Duplicate stop on a terminal run is an explicit conflict.
    dup = client.post(f"/runs/{run_id}/stop", headers=authz)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "already_terminal"


def test_stop_terminal_run_conflicts(client: TestClient, user_headers: dict, tmp_path) -> None:
    src = make_csv(tmp_path / "in.csv", rows=5)
    out = str(tmp_path / "out.csv")
    pipe = create_pipeline(client, user_headers, _csv_pipeline(src, out))
    authz = {"Authorization": user_headers["Authorization"]}
    run_id = client.post(f"/pipelines/{pipe['id']}/runs", headers=authz).json()["id"]
    wait_for_status(client, user_headers, run_id, {"succeeded"})
    resp = client.post(f"/runs/{run_id}/stop", headers=authz)
    assert resp.status_code == 409


def test_run_history_pagination_and_ownership(
    client: TestClient, user_headers: dict, tmp_path
) -> None:
    src = make_csv(tmp_path / "in.csv", rows=5)
    out = str(tmp_path / "out.csv")
    pipe = create_pipeline(client, user_headers, _csv_pipeline(src, out))
    authz = {"Authorization": user_headers["Authorization"]}
    run_id = client.post(f"/pipelines/{pipe['id']}/runs", headers=authz).json()["id"]
    wait_for_status(client, user_headers, run_id, {"succeeded"})
    history = client.get("/runs", headers=authz).json()
    assert history["total"] == 1
    filtered = client.get(f"/runs?pipeline_id={pipe['id']}", headers=authz).json()
    assert filtered["total"] == 1
    # Another user sees neither the run nor its logs.
    email = f"o{uuid.uuid4().hex[:8]}@example.com"
    client.post("/auth/register", json={"email": email, "password": "password123"})
    other = client.post("/auth/login", json={"email": email, "password": "password123"}).json()
    other_authz = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get(f"/runs/{run_id}", headers=other_authz).status_code == 404
    assert client.get("/runs", headers=other_authz).json()["total"] == 0


def test_start_run_missing_pipeline_404(client: TestClient, user_headers: dict) -> None:
    authz = {"Authorization": user_headers["Authorization"]}
    resp = client.post(f"/pipelines/{uuid.uuid4()}/runs", headers=authz)
    assert resp.status_code == 404
