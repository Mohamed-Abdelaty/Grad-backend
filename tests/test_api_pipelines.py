"""Pipeline CRUD tests: validation, versioning, pagination, ownership."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import _valid_doc, create_pipeline


def test_create_and_read_pipeline(client: TestClient, user_headers: dict) -> None:
    body = create_pipeline(client, user_headers, _valid_doc(), name="orders")
    assert body["name"] == "orders"
    assert body["current_version"] == 1
    assert body["config"]["pipeline"]["name"] == "orders"
    got = client.get(
        f"/pipelines/{body['id']}", headers={"Authorization": user_headers["Authorization"]}
    )
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


def test_create_rejects_invalid_config(client: TestClient, user_headers: dict) -> None:
    doc = _valid_doc()
    doc["pipeline"]["edges"].append({"from": "src_1", "to": "ghost"})
    resp = client.post(
        "/pipelines",
        json={"name": "bad", "config": doc},
        headers={"Authorization": user_headers["Authorization"]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_pipeline_config"


def test_create_rejects_unknown_plugin(client: TestClient, user_headers: dict) -> None:
    doc = _valid_doc()
    doc["pipeline"]["nodes"][0]["plugin"] = "ghost"
    resp = client.post(
        "/pipelines",
        json={"name": "bad", "config": doc},
        headers={"Authorization": user_headers["Authorization"]},
    )
    assert resp.status_code == 422


def test_update_creates_new_version_and_preserves_history(
    client: TestClient, user_headers: dict
) -> None:
    authz = {"Authorization": user_headers["Authorization"]}
    body = create_pipeline(client, user_headers, _valid_doc(), name="vtest")
    pid = body["id"]
    doc2 = _valid_doc()
    doc2["pipeline"]["nodes"][1]["config"] = {"expression": "amount > 10"}
    upd = client.put(f"/pipelines/{pid}", json={"config": doc2}, headers=authz)
    assert upd.status_code == 200
    assert upd.json()["current_version"] == 2
    versions = client.get(f"/pipelines/{pid}/versions", headers=authz).json()
    assert versions["total"] == 2
    exprs = {
        v["version"]: v["config"]["pipeline"]["nodes"][1]["config"]["expression"]
        for v in versions["items"]
    }
    assert exprs[1] == "amount > 100"  # history preserved, not overwritten
    assert exprs[2] == "amount > 10"


def test_rename_only_keeps_version(client: TestClient, user_headers: dict) -> None:
    authz = {"Authorization": user_headers["Authorization"]}
    body = create_pipeline(client, user_headers, _valid_doc(), name="old")
    upd = client.put(f"/pipelines/{body['id']}", json={"name": "new"}, headers=authz)
    assert upd.status_code == 200
    assert upd.json()["name"] == "new"
    assert upd.json()["current_version"] == 1


def test_delete_pipeline(client: TestClient, user_headers: dict) -> None:
    authz = {"Authorization": user_headers["Authorization"]}
    body = create_pipeline(client, user_headers, _valid_doc())
    assert client.delete(f"/pipelines/{body['id']}", headers=authz).status_code == 204
    assert client.get(f"/pipelines/{body['id']}", headers=authz).status_code == 404


def test_list_pagination(client: TestClient, user_headers: dict) -> None:
    authz = {"Authorization": user_headers["Authorization"]}
    for i in range(3):
        create_pipeline(client, user_headers, _valid_doc(), name=f"p{i}")
    page1 = client.get("/pipelines?page=1&page_size=2", headers=authz).json()
    assert page1["total"] == 3 and len(page1["items"]) == 2
    page2 = client.get("/pipelines?page=2&page_size=2", headers=authz).json()
    assert len(page2["items"]) == 1


def test_ownership_isolation_between_users(client: TestClient, user_headers: dict) -> None:
    email = f"other{uuid.uuid4().hex[:8]}@example.com"
    client.post("/auth/register", json={"email": email, "password": "password123"})
    other_tokens = client.post(
        "/auth/login", json={"email": email, "password": "password123"}
    ).json()
    other = {"Authorization": f"Bearer {other_tokens['access_token']}"}
    body = create_pipeline(client, user_headers, _valid_doc(), name="mine")
    # Other user cannot read / update / delete / list it.
    assert client.get(f"/pipelines/{body['id']}", headers=other).status_code == 404
    assert (
        client.put(f"/pipelines/{body['id']}", json={"name": "x"}, headers=other).status_code == 404
    )
    assert client.delete(f"/pipelines/{body['id']}", headers=other).status_code == 404
    assert client.get("/pipelines", headers=other).json()["total"] == 0


def test_admin_sees_all_pipelines(client: TestClient, user_headers: dict) -> None:
    from app.core.security import hash_password
    from app.db.session import SessionLocal
    from app.models.entities import User

    create_pipeline(client, user_headers, _valid_doc(), name="user-pipe")
    db = SessionLocal()
    try:
        admin = User(
            email=f"admin{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("password123"),
            role="admin",
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        admin_id = str(admin.id)
    finally:
        db.close()
    from app.core.security import create_access_token

    admin_headers = {
        "Authorization": f"Bearer {create_access_token(user_id=admin_id, role='admin')}"
    }
    assert client.get("/pipelines", headers=admin_headers).json()["total"] >= 1
