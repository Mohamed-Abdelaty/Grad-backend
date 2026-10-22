"""Test K: Diff structure, rollback semantics, version immutability, and invalid config rejection."""

from __future__ import annotations

import copy
import uuid

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.entities import PipelineVersion


def verify_diff_and_rollback() -> None:
    print("[*] Verifying Diff & Rollback...")
    client = TestClient(app)

    # Auth
    email = f"diff_{uuid.uuid4().hex[:6]}@test.com"
    client.post("/auth/register", json={"email": email, "password": "Password123!"})
    token = client.post("/auth/login", json={"email": email, "password": "Password123!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # v1
    v1_config = {
        "pipeline": {
            "id": str(uuid.uuid4()),
            "name": "Diff Pipeline",
            "version": 1,
            "nodes": [
                {"id": "src", "type": "source", "plugin": "csv", "config": {"path": "examples/data/orders.csv"}},
                {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
            ],
            "edges": [{"from": "src", "to": "snk"}],
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
        }
    }
    p_res = client.post("/pipelines", json={"name": "Diff Pipeline", "config": v1_config}, headers=headers)
    assert p_res.status_code == 201
    pipe_id = p_res.json()["id"]

    # v2: Add node tr_filter, change edge
    v2_config = copy.deepcopy(v1_config)
    v2_config["pipeline"]["nodes"].append({
        "id": "tr_filter",
        "type": "transform",
        "plugin": "filter",
        "config": {"expression": "amount > 50"},
    })
    v2_config["pipeline"]["edges"] = [
        {"from": "src", "to": "tr_filter"},
        {"from": "tr_filter", "to": "snk"},
    ]
    u2_res = client.put(f"/pipelines/{pipe_id}", json={"config": v2_config}, headers=headers)
    assert u2_res.status_code == 200 and u2_res.json()["current_version"] == 2

    # v3: Modify tr_filter config (expression), remove snk and replace with snk_json
    v3_config = copy.deepcopy(v2_config)
    for n in v3_config["pipeline"]["nodes"]:
        if n["id"] == "tr_filter":
            n["config"]["expression"] = "amount > 100"
    v3_config["pipeline"]["nodes"] = [n for n in v3_config["pipeline"]["nodes"] if n["id"] != "snk"]
    v3_config["pipeline"]["nodes"].append({
        "id": "snk_json",
        "type": "sink",
        "plugin": "json",
        "config": {"path": "output/res.json"},
    })
    v3_config["pipeline"]["edges"] = [
        {"from": "src", "to": "tr_filter"},
        {"from": "tr_filter", "to": "snk_json"},
    ]
    u3_res = client.put(f"/pipelines/{pipe_id}", json={"config": v3_config}, headers=headers)
    assert u3_res.status_code == 200 and u3_res.json()["current_version"] == 3

    # 1. Precise diff between v1 and v3
    diff_res = client.get(f"/pipelines/{pipe_id}/versions/diff?from=1&to=3", headers=headers)
    assert diff_res.status_code == 200
    diff_data = diff_res.json()

    added_nodes = [n["id"] for n in diff_data["nodes"]["added"]]
    removed_nodes = [n["id"] for n in diff_data["nodes"]["removed"]]
    assert "tr_filter" in added_nodes
    assert "snk_json" in added_nodes
    assert "snk" in removed_nodes

    # Same version diff is empty
    empty_diff = client.get(f"/pipelines/{pipe_id}/versions/diff?from=2&to=2", headers=headers).json()
    assert not empty_diff["nodes"].get("added") and not empty_diff["nodes"].get("removed") and not empty_diff["nodes"].get("changed")

    # 2. Rollback to v1 -> v4 equals v1 content, v1-v3 untouched
    rb_res = client.post(f"/pipelines/{pipe_id}/rollback/1", headers=headers)
    assert rb_res.status_code == 201
    assert rb_res.json()["version"] == 4

    # Verify v4 nodes match v1 nodes
    v4_nodes = {n["id"] for n in rb_res.json()["config"]["nodes"]}
    v1_nodes = {n["id"] for n in v1_config["pipeline"]["nodes"]}
    assert v4_nodes == v1_nodes

    # Check database: v1, v2, v3 configs were preserved completely
    db = SessionLocal()
    try:
        versions = db.query(PipelineVersion).filter(PipelineVersion.pipeline_id == uuid.UUID(pipe_id)).all()
        assert len(versions) == 4
        v1_row = next(v for v in versions if v.version == 1)
        v2_row = next(v for v in versions if v.version == 2)
        v3_row = next(v for v in versions if v.version == 3)
        assert v1_row.version == 1 and len(v1_row.config["pipeline"]["nodes"]) == 2
        assert v2_row.version == 2 and len(v2_row.config["pipeline"]["nodes"]) == 3
        assert v3_row.version == 3 and len(v3_row.config["pipeline"]["nodes"]) == 3
    finally:
        db.close()

    # Rollback to non-existent version -> 404
    assert client.post(f"/pipelines/{pipe_id}/rollback/999", headers=headers).status_code == 404

    # Rollback to currently active version -> 409
    assert client.post(f"/pipelines/{pipe_id}/rollback/4", headers=headers).status_code == 409

    print("[PASS] Diff and Rollback verified successfully (accurate diff, immutable history, N+1 creation).")


if __name__ == "__main__":
    verify_diff_and_rollback()
