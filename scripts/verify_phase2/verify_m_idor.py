"""Test M: IDOR sweep across all Phase 2 resources."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def verify_idor_sweep() -> None:
    print("[*] Running IDOR Sweep across User A and User B resources...")
    client = TestClient(app)

    # Register User A
    email_a = f"idor_a_{uuid.uuid4().hex[:6]}@test.com"
    client.post("/auth/register", json={"email": email_a, "password": "Password123!"})
    token_a = client.post("/auth/login", json={"email": email_a, "password": "Password123!"}).json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Register User B
    email_b = f"idor_b_{uuid.uuid4().hex[:6]}@test.com"
    client.post("/auth/register", json={"email": email_b, "password": "Password123!"})
    token_b = client.post("/auth/login", json={"email": email_b, "password": "Password123!"}).json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User B creates pipeline, schedule, template, key, and notification
    pipe_b = client.post(
        "/pipelines",
        json={
            "name": "User B Pipeline",
            "config": {
                "pipeline": {
                    "id": str(uuid.uuid4()),
                    "name": "User B Pipeline",
                    "version": 1,
                    "nodes": [
                        {"id": "s", "type": "source", "plugin": "csv", "config": {"path": "examples/data/orders.csv"}},
                        {"id": "k", "type": "sink", "plugin": "console", "config": {}},
                    ],
                    "edges": [{"from": "s", "to": "k"}],
                    "retry": {"max_attempts": 1, "backoff_seconds": 0},
                }
            },
        },
        headers=headers_b,
    ).json()
    pipe_b_id = pipe_b["id"]

    # User B creates schedule
    sched_b = client.post(
        f"/pipelines/{pipe_b_id}/schedule",
        json={"cron": "0 0 * * *", "timezone": "UTC"},
        headers=headers_b,
    ).json()

    # User B creates private template
    tpl_b = client.post(
        "/templates",
        json={
            "name": "B Private Template",
            "category": "ETL",
            "description": "Private to B",
            "config": pipe_b["config"],
        },
        headers=headers_b,
    ).json()
    tpl_b_id = tpl_b["id"]

    # User B creates API key
    key_b = client.post("/api-keys", json={"name": "key-b", "scopes": ["read"]}, headers=headers_b).json()
    key_b_id = key_b["id"]

    # User A tries to access User B's resources:
    # 1. Schedule GET
    res = client.get(f"/pipelines/{pipe_b_id}/schedule", headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could get User B's schedule! Status {res.status_code}"

    # 2. Schedule PUT
    res = client.put(f"/pipelines/{pipe_b_id}/schedule", json={"cron": "1 1 * * *"}, headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could update User B's schedule! Status {res.status_code}"

    # 3. Schedule DELETE
    res = client.delete(f"/pipelines/{pipe_b_id}/schedule", headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could delete User B's schedule! Status {res.status_code}"

    # 4. Template GET
    res = client.get(f"/templates/{tpl_b_id}", headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could get User B's private template! Status {res.status_code}"

    # 5. Template UPDATE
    res = client.put(f"/templates/{tpl_b_id}", json={"name": "Hacked"}, headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could update User B's template! Status {res.status_code}"

    # 6. Template DELETE
    res = client.delete(f"/templates/{tpl_b_id}", headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could delete User B's template! Status {res.status_code}"

    # 7. Template USE
    res = client.post(f"/templates/{tpl_b_id}/use", headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could use User B's private template! Status {res.status_code}"

    # 8. API Key REVOKE
    res = client.request("DELETE", f"/api-keys/{key_b_id}", json={"confirm": True}, headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could revoke User B's API key! Status {res.status_code}"

    # 9. Pipeline diff on B's pipeline
    res = client.get(f"/pipelines/{pipe_b_id}/versions/diff?from=1&to=1", headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could diff User B's pipeline! Status {res.status_code}"

    # 10. Pipeline rollback on B's pipeline
    res = client.post(f"/pipelines/{pipe_b_id}/rollback/1", headers=headers_a)
    assert res.status_code == 404, f"IDOR: User A could rollback User B's pipeline! Status {res.status_code}"

    print("[PASS] IDOR sweep verified successfully (all unauthorized cross-user operations return 404).")


if __name__ == "__main__":
    verify_idor_sweep()
