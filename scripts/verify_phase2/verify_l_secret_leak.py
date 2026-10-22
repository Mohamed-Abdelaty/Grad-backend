"""Test L: Secret leak sweep for canary API key and webhook secret."""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.entities import ApiKey, AuditEvent, NotificationPreference


def verify_secret_leak_sweep() -> None:
    print("[*] Running Secret Leak Sweep with canary tokens...")
    client = TestClient(app)

    # 1. Register user
    email = f"canary_{uuid.uuid4().hex[:6]}@test.com"
    client.post("/auth/register", json={"email": email, "password": "Password123!"})
    token = client.post("/auth/login", json={"email": email, "password": "Password123!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create API key with unique canary secret
    canary_key_name = "canary-api-key"
    key_res = client.post("/api-keys", json={"name": canary_key_name, "scopes": ["read", "run"]}, headers=headers)
    assert key_res.status_code == 201
    raw_canary_key = key_res.json()["key"]
    # The random secret part of the key
    canary_secret_part = raw_canary_key.split("_")[-1]

    # 3. Save canary webhook secret
    canary_webhook_secret = f"wh_canary_secret_{uuid.uuid4().hex}"
    wh_res = client.put(
        "/notifications/preferences/webhook",
        json={"url": "https://example.com/webhook", "secret": canary_webhook_secret},
        headers=headers,
    )
    assert wh_res.status_code == 200
    # Must NOT return secret
    assert wh_res.json().get("secret") == "", "Webhook secret must NOT be returned in response!"

    # 4. Read list of API keys - must only show prefix, not secret
    keys_list = client.get("/api-keys", headers=headers).json()
    assert canary_secret_part not in str(keys_list), "Canary key secret leaked in /api-keys list!"

    # 5. Check Database directly
    db = SessionLocal()
    try:
        # Check api_keys table
        all_keys = db.query(ApiKey).all()
        for k in all_keys:
            assert canary_secret_part not in (k.key_hash or ""), "Raw canary key secret found in key_hash!"
            assert canary_secret_part not in (k.prefix or ""), "Raw canary key secret found in prefix!"

        # Check webhook preferences table
        all_prefs = db.query(NotificationPreference).all()
        for p in all_prefs:
            assert canary_webhook_secret not in (p.webhook_secret_encrypted or ""), (
                "Plaintext webhook secret leaked in notification_preferences DB!"
            )

        # Check audit_events table
        all_audit = db.query(AuditEvent).all()
        for a in all_audit:
            audit_str = f"{a.actor} {a.action} {a.event_metadata}"
            assert canary_secret_part not in audit_str, "Raw API key canary leaked in audit_events table!"
            assert canary_webhook_secret not in audit_str, "Canary webhook secret leaked in audit_events table!"
    finally:
        db.close()

    # 6. Check log file if app.log exists
    if os.path.exists("app.log"):
        with open("app.log", encoding="utf-8", errors="ignore") as f:
            log_content = f.read()
            assert canary_secret_part not in log_content, "Canary key secret leaked in app.log!"
            assert canary_webhook_secret not in log_content, "Canary webhook secret leaked in app.log!"

    print("[PASS] Secret leak sweep verified successfully (no raw keys or webhook secrets leaked).")


if __name__ == "__main__":
    verify_secret_leak_sweep()
