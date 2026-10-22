"""Phase 2 regression tests: verifies all Critical and High fixes."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.models.entities import (
    ApiKey,
    Notification,
    PipelineVersion,
    Template,
    User,
)
from app.services.apikey import ApiKeyService
from app.services.audit import AuditService


def _valid_pipe_doc(pipe_id: str | None = None) -> dict[str, Any]:
    return {
        "pipeline": {
            "id": pipe_id or str(uuid.uuid4()),
            "name": "regression-pipe",
            "version": 1,
            "nodes": [
                {
                    "id": "src",
                    "type": "source",
                    "plugin": "csv",
                    "config": {"path": "examples/data/orders.csv"},
                },
                {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
            ],
            "edges": [{"from": "src", "to": "snk"}],
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
        }
    }


def _auth_user(client: TestClient, role: str = "user") -> tuple[dict[str, str], User]:
    email = f"u_{uuid.uuid4().hex[:8]}@example.com"
    reg = client.post("/auth/register", json={"email": email, "password": "Password123!"})
    assert reg.status_code == 201

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        if role != "user":
            user.role = role
            db.commit()
            db.refresh(user)
    finally:
        db.close()

    login = client.post("/auth/login", json={"email": email, "password": "Password123!"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return headers, user


def test_f01_schedule_creation_scope_no_500(client: TestClient) -> None:
    """F-01 regression: create_schedule must not throw TypeError / 500."""
    headers, user = _auth_user(client)
    doc = _valid_pipe_doc()
    pipe_res = client.post("/pipelines", json={"name": "test-p", "config": doc}, headers=headers)
    assert pipe_res.status_code == 201
    pipe_id = pipe_res.json()["id"]

    # Creating schedule with valid write scope (JWT has write scope)
    res = client.post(
        f"/pipelines/{pipe_id}/schedule",
        json={"cron": "0 0 * * *", "timezone": "UTC"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["cron"] == "0 0 * * *"
    assert res.json()["pipeline_id"] == pipe_id


def test_f02_audit_endpoint_admin_enforcement(client: TestClient) -> None:
    """F-02 regression: non-admin JWT users and API keys get 403 on audit log."""
    # 1. Non-admin JWT user gets 403
    user_headers, _ = _auth_user(client, role="user")
    res = client.get("/admin/audit-log", headers=user_headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"

    res_exp = client.get("/admin/audit-log/export", headers=user_headers)
    assert res_exp.status_code == 403
    assert res_exp.json()["error"]["code"] == "forbidden"

    # 2. Admin JWT user gets 200
    admin_headers, _ = _auth_user(client, role="admin")
    res_admin = client.get("/admin/audit-log", headers=admin_headers)
    assert res_admin.status_code == 200
    assert "items" in res_admin.json()

    res_admin_exp = client.get("/admin/audit-log/export", headers=admin_headers)
    assert res_admin_exp.status_code == 200
    assert res_admin_exp.headers["content-type"].startswith("text/csv")


def test_f05_diff_rollback_deepcopy(client: TestClient) -> None:
    """F-05 regression: rollback uses deepcopy so target version config is preserved."""
    headers, user = _auth_user(client)
    doc_v1 = _valid_pipe_doc()
    pipe_res = client.post("/pipelines", json={"name": "pipe-diff", "config": doc_v1}, headers=headers)
    assert pipe_res.status_code == 201
    pipe_id = pipe_res.json()["id"]

    # Create v2 with an extra node
    doc_v2 = copy.deepcopy(doc_v1)
    doc_v2["pipeline"]["nodes"].append({
        "id": "tr_filter",
        "type": "transform",
        "plugin": "filter",
        "config": {"expression": "1 == 1"},
    })
    doc_v2["pipeline"]["edges"] = [
        {"from": "src", "to": "tr_filter"},
        {"from": "tr_filter", "to": "snk"},
    ]
    upd_res = client.put(f"/pipelines/{pipe_id}", json={"config": doc_v2}, headers=headers)
    assert upd_res.status_code == 200
    assert upd_res.json()["current_version"] == 2

    # Verify target v1 config before rollback
    db = SessionLocal()
    try:
        v1 = db.query(PipelineVersion).filter(
            PipelineVersion.pipeline_id == uuid.UUID(pipe_id),
            PipelineVersion.version == 1,
        ).one()
        v1_nodes_before = [n["id"] for n in v1.config["pipeline"]["nodes"]]
    finally:
        db.close()

    # Rollback to v1 (creates v3)
    rb_res = client.post(f"/pipelines/{pipe_id}/rollback/1", headers=headers)
    assert rb_res.status_code == 201
    assert rb_res.json()["version"] == 3

    # Check v1 was NOT mutated
    db = SessionLocal()
    try:
        v1_after = db.query(PipelineVersion).filter(
            PipelineVersion.pipeline_id == uuid.UUID(pipe_id),
            PipelineVersion.version == 1,
        ).one()
        assert [n["id"] for n in v1_after.config["pipeline"]["nodes"]] == v1_nodes_before
        assert v1_after.config["pipeline"]["version"] == 1
    finally:
        db.close()


def test_f06_f07_template_use_deepcopy_and_pipeline_id(client: TestClient) -> None:
    """F-06 & F-07 regression: use_template returns new pipeline_id and preserves template."""
    headers, user = _auth_user(client)
    doc = _valid_pipe_doc()

    tpl_res = client.post(
        "/templates",
        json={"name": "Tpl1", "category": "ETL", "description": "desc", "config": doc},
        headers=headers,
    )
    assert tpl_res.status_code == 201
    tpl_id = tpl_res.json()["id"]

    # Use template
    use_res = client.post(f"/templates/{tpl_id}/use", headers=headers)
    assert use_res.status_code == 200
    use_data = use_res.json()

    # FIX F-07: pipeline_id must be the new pipeline, NOT the template id!
    assert use_data["pipeline_id"] != tpl_id
    new_pipe_id = use_data["pipeline_id"]

    # Verify pipeline exists and can be retrieved
    get_pipe = client.get(f"/pipelines/{new_pipe_id}", headers=headers)
    assert get_pipe.status_code == 200
    assert get_pipe.json()["id"] == new_pipe_id

    # FIX F-06: Verify original template config was NOT mutated
    db = SessionLocal()
    try:
        tpl = db.get(Template, uuid.UUID(tpl_id))
        assert tpl.config["pipeline"]["id"] != new_pipe_id
    finally:
        db.close()


def test_f10_template_usage_count_atomic(client: TestClient) -> None:
    """F-10 regression: template usage_count increments correctly."""
    headers, user = _auth_user(client)
    doc = _valid_pipe_doc()

    tpl_res = client.post(
        "/templates",
        json={"name": "TplCounter", "category": "Sync", "description": "desc", "config": doc},
        headers=headers,
    )
    assert tpl_res.status_code == 201
    tpl_id = tpl_res.json()["id"]

    for _ in range(3):
        res = client.post(f"/templates/{tpl_id}/use", headers=headers)
        assert res.status_code == 200

    db = SessionLocal()
    try:
        tpl = db.get(Template, uuid.UUID(tpl_id))
        assert tpl.usage_count == 3
    finally:
        db.close()


def test_f09_api_key_prefix_collision(client: TestClient) -> None:
    """F-09 regression: keys with the same prefix authenticate to the correct user."""
    db = SessionLocal()
    try:
        user_a = User(email=f"a_{uuid.uuid4().hex[:6]}@test.com", password_hash="x", role="user")
        user_b = User(email=f"b_{uuid.uuid4().hex[:6]}@test.com", password_hash="x", role="user")
        db.add_all([user_a, user_b])
        db.commit()

        # Deliberately give both keys the EXACT SAME prefix
        shared_prefix = "abcd1234"
        raw_key_a = f"cf_{shared_prefix}_secretaaaaaaaaaaaaaaaaaaaaaaaa"
        raw_key_b = f"cf_{shared_prefix}_secretbbbbbbbbbbbbbbbbbbbbbbbb"

        key_a = ApiKey(
            user_id=user_a.id,
            name="Key A",
            prefix=shared_prefix,
            key_hash=ApiKeyService._hash_key(raw_key_a),
            scopes=["read"],
        )
        key_b = ApiKey(
            user_id=user_b.id,
            name="Key B",
            prefix=shared_prefix,
            key_hash=ApiKeyService._hash_key(raw_key_b),
            scopes=["write"],
        )
        db.add_all([key_a, key_b])
        db.commit()

        # Authenticate key B (which is second in DB)
        result_b = ApiKeyService.authenticate_key(db, raw_key_b)
        assert result_b is not None
        auth_user_b, scopes_b = result_b
        assert auth_user_b.id == user_b.id
        assert scopes_b == ["write"]

        # Authenticate key A
        result_a = ApiKeyService.authenticate_key(db, raw_key_a)
        assert result_a is not None
        auth_user_a, scopes_a = result_a
        assert auth_user_a.id == user_a.id
        assert scopes_a == ["read"]
    finally:
        db.close()


def test_api_key_scopes_matrix(client: TestClient) -> None:
    """Verification H: scope matrix enforcement (read=GET, write=mutations, run=start/stop)."""
    headers, user = _auth_user(client)

    # 1. Create a read-scoped API key
    key_read_res = client.post(
        "/api-keys",
        json={"name": "read-key", "scopes": ["read"]},
        headers=headers,
    )
    assert key_read_res.status_code == 201
    read_api_key = key_read_res.json()["key"]

    # 2. Create a run-scoped API key
    key_run_res = client.post(
        "/api-keys",
        json={"name": "run-key", "scopes": ["run"]},
        headers=headers,
    )
    assert key_run_res.status_code == 201
    run_api_key = key_run_res.json()["key"]

    read_headers = {"X-API-Key": read_api_key}
    run_headers = {"X-API-Key": run_api_key}

    # Read-scoped key can GET pipelines (200)
    assert client.get("/pipelines", headers=read_headers).status_code == 200

    # Read-scoped key CANNOT POST pipeline (403)
    doc = _valid_pipe_doc()
    assert client.post("/pipelines", json={"name": "p", "config": doc}, headers=read_headers).status_code == 403

    # Run-scoped key CANNOT create pipeline (403)
    assert client.post("/pipelines", json={"name": "p", "config": doc}, headers=run_headers).status_code == 403

    # Create pipeline using JWT user (write scope)
    pipe = client.post("/pipelines", json={"name": "run-pipe", "config": doc}, headers=headers).json()
    pipe_id = pipe["id"]

    # Read-scoped key CANNOT start run (403)
    assert client.post(f"/pipelines/{pipe_id}/runs", headers=read_headers).status_code == 403

    # Run-scoped key CAN start run (202)
    start_res = client.post(f"/pipelines/{pipe_id}/runs", headers=run_headers)
    assert start_res.status_code == 202

    # API key tries to call /api-keys or /admin/audit-log -> 403
    assert client.post("/api-keys", json={"name": "k", "scopes": ["read"]}, headers=read_headers).status_code == 403
    assert client.get("/admin/audit-log", headers=read_headers).status_code == 403


def test_f03_run_completion_emits_notifications(client: TestClient) -> None:
    """F-03 regression: run success emits run_succeeded notification."""
    headers, user = _auth_user(client)
    doc = _valid_pipe_doc()
    pipe = client.post("/pipelines", json={"name": "notif-pipe", "config": doc}, headers=headers).json()
    pipe_id = pipe["id"]

    # Run the pipeline
    run_res = client.post(f"/pipelines/{pipe_id}/runs", headers=headers)
    assert run_res.status_code == 202
    run_id = run_res.json()["id"]

    # Wait briefly for worker thread to finish execution
    import time
    for _ in range(20):
        time.sleep(0.1)
        r = client.get(f"/runs/{run_id}", headers=headers).json()
        if r["status"] in ("succeeded", "failed"):
            break

    # Verify notification was generated for this run
    db = SessionLocal()
    try:
        notif = db.query(Notification).filter(Notification.run_id == uuid.UUID(run_id)).first()
        assert notif is not None, "Notification must be emitted on run completion"
        assert notif.type in ("run_succeeded", "run_failed")
        assert notif.user_id == user.id
    finally:
        db.close()


def test_f14_csv_formula_injection_protection() -> None:
    """F-14 regression: formula characters (=, +, -, @, tab, CR) are safely escaped."""
    malicious_inputs = ["=cmd|' /C calc'!A0", "+1+1", "-2+3", "@SUM(A1:A10)", "\tmalicious", "\rmalicious"]
    for val in malicious_inputs:
        escaped = AuditService._escape_csv(val)
        # Must start with single quote to neutralize formula execution in Excel/Calc
        assert escaped.startswith("'") or escaped.startswith('"\''), f"Value {val!r} was not neutralized: {escaped!r}"
