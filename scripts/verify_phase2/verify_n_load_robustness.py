"""Test N: Robustness, payload fuzzing, unicode handling, and health check responsiveness."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app


def verify_load_and_robustness() -> None:
    print("[*] Running Load & Robustness checks...")
    client = TestClient(app)

    # 1. Health check stays responsive
    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"

    # 2. Auth for tests
    email = f"fuzz_{uuid.uuid4().hex[:6]}@test.com"
    client.post("/auth/register", json={"email": email, "password": "Password123!"})
    token = client.post("/auth/login", json={"email": email, "password": "Password123!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Unicode in names
    unicode_name = "???? Pipeline ???? ??????? ???????? ???"
    pipe_res = client.post(
        "/pipelines",
        json={
            "name": unicode_name,
            "config": {
                "pipeline": {
                    "id": str(uuid.uuid4()),
                    "name": unicode_name,
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
        headers=headers,
    )
    assert pipe_res.status_code == 201
    pipe_id = pipe_res.json()["id"]
    assert pipe_res.json()["name"] == unicode_name

    # 4. Fuzzing invalid cron expressions -> 422 standard format
    invalid_crons = ["99 * * * *", "not-a-cron", "* * * * * * *", "", "-1 0 * * *"]
    for ic in invalid_crons:
        res = client.post(f"/pipelines/{pipe_id}/schedule", json={"cron": ic, "timezone": "UTC"}, headers=headers)
        assert res.status_code == 422, f"Cron {ic!r} should be 422, got {res.status_code}"
        assert "error" in res.json()
        assert res.json()["error"]["code"] == "validation_error"

    # 5. Fuzzing invalid timezones -> 422 standard format
    invalid_tzs = ["Mars/Base_1", "Invalid/Timezone", "UTC+99", ""]
    for itz in invalid_tzs:
        res = client.post(f"/pipelines/{pipe_id}/schedule", json={"cron": "0 0 * * *", "timezone": itz}, headers=headers)
        assert res.status_code == 422
        assert "error" in res.json()
        assert res.json()["error"]["code"] == "validation_error"

    # 6. Oversized webhook URL (exceeding max_length=512) -> 422
    oversized_url = "https://example.com/" + "a" * 600
    res = client.put(
        "/notifications/preferences/webhook",
        json={"url": oversized_url, "secret": "valid_secret_length_123"},
        headers=headers,
    )
    assert res.status_code == 422
    assert "error" in res.json()

    # 7. Health check still 200 after all fuzzing
    assert client.get("/health").status_code == 200

    print("[PASS] Load and Robustness verified successfully (health responsive, unicode preserved, fuzzing returns 422 standard errors).")


if __name__ == "__main__":
    verify_load_and_robustness()
