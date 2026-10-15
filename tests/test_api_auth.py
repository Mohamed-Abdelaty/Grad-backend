"""Auth integration tests: register/login/refresh, failures, hashing, roles."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def _register(client: TestClient, email: str, password: str = "password123") -> dict:
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_register_and_login_flow(client: TestClient) -> None:
    email = f"a{uuid.uuid4().hex[:8]}@example.com"
    body = _register(client, email)
    assert body["email"] == email
    assert body["role"] == "user"
    assert "password" not in body and "password_hash" not in body
    login = client.post("/auth/login", json={"email": email, "password": "password123"})
    assert login.status_code == 200
    tokens = login.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]
    me = client.get("/pipelines", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200


def test_register_duplicate_email_conflict(client: TestClient) -> None:
    email = f"d{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    resp = client.post("/auth/register", json={"email": email, "password": "password123"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "already_exists"


def test_register_never_grants_admin(client: TestClient) -> None:
    email = f"r{uuid.uuid4().hex[:8]}@example.com"
    body = _register(client, email)
    assert body["role"] == "user"


def test_login_invalid_credentials(client: TestClient) -> None:
    email = f"l{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    bad = client.post("/auth/login", json={"email": email, "password": "wrongpass1"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_credentials"
    unknown = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "password123"}
    )
    assert unknown.status_code == 401


def test_passwords_hashed_not_plaintext(client: TestClient) -> None:
    from app.db.session import SessionLocal
    from app.models.entities import User

    email = f"h{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email, password="s3cretpass")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        assert user.password_hash != "s3cretpass"
        assert user.password_hash.startswith("$2b$")
    finally:
        db.close()


def test_refresh_rotation_and_reuse_rejected(client: TestClient) -> None:
    email = f"f{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    tokens = client.post("/auth/login", json={"email": email, "password": "password123"}).json()
    first = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200
    rotated = first.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]
    reuse = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401  # old token revoked by rotation


def test_refresh_with_access_token_rejected(client: TestClient) -> None:
    email = f"g{uuid.uuid4().hex[:8]}@example.com"
    _register(client, email)
    tokens = client.post("/auth/login", json={"email": email, "password": "password123"}).json()
    resp = client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert resp.status_code == 401


def test_unauthenticated_access_rejected(client: TestClient) -> None:
    assert client.get("/pipelines").status_code == 401
    assert client.get("/pipelines", headers={"Authorization": "Bearer bogus"}).status_code == 401


def test_api_prefix_parity_for_auth(client: TestClient) -> None:
    email = f"p{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert resp.status_code == 201
