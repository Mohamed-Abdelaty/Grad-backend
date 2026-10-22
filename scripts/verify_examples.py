"""Automated Verification of all documented examples in docs/API_TEST_GUIDE.md.

Executes real HTTP requests against the FastAPI TestClient, verifying status codes,
permission boundaries, and realistic payloads. Prints a markdown results table.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app


def run_verification() -> list[dict[str, Any]]:
    app = create_app()
    client = TestClient(app)
    results: list[dict[str, Any]] = []

    def check(endpoint: str, method: str, expected_status: int, response_status: int, note: str = "") -> None:
        status_str = "PASS" if expected_status == response_status else "FAIL"
        results.append({
            "endpoint": f"{method} {endpoint}",
            "expected": expected_status,
            "actual": response_status,
            "result": status_str,
            "note": note,
        })

    print("[*] Starting verification of all API Guide examples...")

    # Group 11: Health
    res = client.get("/health")
    check("/health", "GET", 200, res.status_code)

    # Group 1: Auth
    # Use unique emails per verification run to verify 201 Created flow
    import time
    timestamp_suffix = str(int(time.time() * 1000))
    email_a = f"test_a_{timestamp_suffix}@example.com"
    email_b = f"test_b_{timestamp_suffix}@example.com"

    # 1.1 Register User A
    with open("samples/api/register_user_a.json") as f:
        body_reg_a = json.load(f)
    body_reg_a["email"] = email_a
    res = client.post("/auth/register", json=body_reg_a)
    check("/auth/register (User A)", "POST", 201, res.status_code)

    # 1.1 Negative: Register missing password
    res = client.post("/auth/register", json={"email": "bad@example.com"})
    check("/auth/register (Invalid body)", "POST", 422, res.status_code)

    # 1.2 Register User B
    with open("samples/api/register_user_b.json") as f:
        body_reg_b = json.load(f)
    body_reg_b["email"] = email_b
    res = client.post("/auth/register", json=body_reg_b)
    check("/auth/register (User B)", "POST", 201, res.status_code)

    # 1.3 Login User A
    res = client.post("/auth/login", json={"email": email_a, "password": "Password123!"})
    check("/auth/login (User A)", "POST", 200, res.status_code)
    token_a = res.json()["access_token"]
    refresh_a = res.json()["refresh_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 1.3 Negative: Bad password
    res = client.post("/auth/login", json={"email": email_a, "password": "WrongPassword"})
    check("/auth/login (Bad Password)", "POST", 401, res.status_code)

    # 1.4 Login Admin
    res = client.post("/auth/login", json={"email": "admin@test.com", "password": "adminpassword"})
    check("/auth/login (Admin)", "POST", 200, res.status_code)
    token_admin = res.json().get("access_token", "")
    headers_admin = {"Authorization": f"Bearer {token_admin}"}

    # 1.5 Refresh
    res = client.post("/auth/refresh", json={"refresh_token": refresh_a})
    check("/auth/refresh", "POST", 200, res.status_code)

    # Group 5: Plugins
    res = client.get("/plugins")
    check("/plugins", "GET", 200, res.status_code)

    # Group 2: Pipelines
    # 2.1 Create Pipeline
    with open("samples/api/create_pipeline.json") as f:
        pipe_body = json.load(f)
    res = client.post("/pipelines", json=pipe_body, headers=headers_a)
    check("/pipelines", "POST", 201, res.status_code)
    pipeline_id = res.json()["id"]

    # 2.1 Negative: Create invalid pipeline
    res = client.post("/pipelines", json={"name": "x", "config": {}}, headers=headers_a)
    check("/pipelines (Invalid config)", "POST", 422, res.status_code)

    # 2.2 List Pipelines
    res = client.get("/pipelines", headers=headers_a)
    check("/pipelines", "GET", 200, res.status_code)

    # 2.3 Get Pipeline
    res = client.get(f"/pipelines/{pipeline_id}", headers=headers_a)
    check("/pipelines/{id}", "GET", 200, res.status_code)

    # 2.4 Update Pipeline
    with open("samples/api/update_pipeline.json") as f:
        update_pipe_body = json.load(f)
    res = client.put(f"/pipelines/{pipeline_id}", json=update_pipe_body, headers=headers_a)
    check("/pipelines/{id}", "PUT", 200, res.status_code)

    # Group 3: Versions, Diff, Rollback
    # 3.1 List Versions
    res = client.get(f"/pipelines/{pipeline_id}/versions", headers=headers_a)
    check("/pipelines/{id}/versions", "GET", 200, res.status_code)

    # 3.2 Diff Versions
    res = client.get(f"/pipelines/{pipeline_id}/versions/diff?from=1&to=2", headers=headers_a)
    check("/pipelines/{id}/versions/diff", "GET", 200, res.status_code)

    # 3.3 Rollback
    res = client.post(f"/pipelines/{pipeline_id}/rollback/1", headers=headers_a)
    check("/pipelines/{id}/rollback/{v}", "POST", 201, res.status_code)

    # Group 4: Runs
    # 4.1 Start Run
    res = client.post(f"/pipelines/{pipeline_id}/runs", headers=headers_a)
    check("/pipelines/{id}/runs", "POST", 202, res.status_code)
    run_id = res.json()["id"]

    # 4.2 Get Run
    res = client.get(f"/runs/{run_id}", headers=headers_a)
    check("/runs/{id}", "GET", 200, res.status_code)

    # 4.3 Get Run Logs
    res = client.get(f"/runs/{run_id}/logs", headers=headers_a)
    check("/runs/{id}/logs", "GET", 200, res.status_code)

    # 4.4 List Runs
    res = client.get("/runs", headers=headers_a)
    check("/runs", "GET", 200, res.status_code)

    # Group 6: Schedules
    # 6.1 Preview
    res = client.get("/pipelines/schedules/preview?cron=0%20*%20*%20*%20*&timezone=UTC&count=5")
    check("/pipelines/schedules/preview", "GET", 200, res.status_code)

    # 6.1 Negative: bad cron
    res = client.get("/pipelines/schedules/preview?cron=bad_cron&timezone=UTC")
    check("/pipelines/schedules/preview (Bad Cron)", "GET", 422, res.status_code)

    # 6.2 Create Schedule
    with open("samples/api/create_schedule.json") as f:
        sched_body = json.load(f)
    res = client.post(f"/pipelines/{pipeline_id}/schedule", json=sched_body, headers=headers_a)
    check("/pipelines/{id}/schedule", "POST", 201, res.status_code)

    # 6.3 Get Schedule
    res = client.get(f"/pipelines/{pipeline_id}/schedule", headers=headers_a)
    check("/pipelines/{id}/schedule", "GET", 200, res.status_code)

    # 6.4 Update Schedule
    with open("samples/api/update_schedule.json") as f:
        sched_upd = json.load(f)
    res = client.put(f"/pipelines/{pipeline_id}/schedule", json=sched_upd, headers=headers_a)
    check("/pipelines/{id}/schedule", "PUT", 200, res.status_code)

    # Group 7: Templates
    # 7.1 List Templates
    res = client.get("/templates", headers=headers_a)
    check("/templates", "GET", 200, res.status_code)

    # 7.2 Create Template
    with open("samples/api/create_template.json") as f:
        tmpl_body = json.load(f)
    res = client.post("/templates", json=tmpl_body, headers=headers_a)
    check("/templates", "POST", 201, res.status_code)
    template_id = res.json()["id"]

    # 7.3 Use Template
    res = client.post(f"/templates/{template_id}/use", headers=headers_a)
    check("/templates/{id}/use", "POST", 200, res.status_code)

    # 7.4 Save Pipeline As Template
    res = client.post(
        f"/templates/pipelines/{pipeline_id}/save-as-template?name=Saved%20Template&category=ETL&description=Template%20desc",
        headers=headers_a,
    )
    check("/templates/pipelines/{id}/save-as-template", "POST", 201, res.status_code)

    # Group 8: Notifications
    # 8.1 List Notifications
    res = client.get("/notifications", headers=headers_a)
    check("/notifications", "GET", 200, res.status_code)

    # 8.2 Unread Count
    res = client.get("/notifications/unread-count", headers=headers_a)
    check("/notifications/unread-count", "GET", 200, res.status_code)

    # 8.3 Preferences GET
    res = client.get("/notifications/preferences", headers=headers_a)
    check("/notifications/preferences", "GET", 200, res.status_code)

    # 8.4 Preferences PUT
    with open("samples/api/update_notification_preferences.json") as f:
        pref_body = json.load(f)
    res = client.put("/notifications/preferences", json=pref_body, headers=headers_a)
    check("/notifications/preferences", "PUT", 200, res.status_code)

    # 8.5 Webhook config
    with open("samples/api/update_webhook_config.json") as f:
        wh_body = json.load(f)
    res = client.put("/notifications/preferences/webhook", json=wh_body, headers=headers_a)
    check("/notifications/preferences/webhook", "PUT", 200, res.status_code)

    # 8.6 Deliveries
    res = client.get("/notifications/deliveries", headers=headers_a)
    check("/notifications/deliveries", "GET", 200, res.status_code)

    # Group 9: API Keys
    # 9.1 Create Read API Key
    with open("samples/api/create_api_key_read.json") as f:
        key_body = json.load(f)
    res = client.post("/api-keys", json=key_body, headers=headers_a)
    check("/api-keys (Read Key)", "POST", 201, res.status_code)
    read_api_key = res.json()["key"]
    api_key_id = res.json()["id"]

    # 9.2 Create Run API Key
    with open("samples/api/create_api_key_run.json") as f:
        key_run_body = json.load(f)
    res = client.post("/api-keys", json=key_run_body, headers=headers_a)
    check("/api-keys (Run Key)", "POST", 201, res.status_code)
    run_api_key = res.json()["key"]

    # 9.3 Scope enforcement: Read key GET /pipelines
    res = client.get("/pipelines", headers={"X-API-Key": read_api_key})
    check("/pipelines (with Read Key)", "GET", 200, res.status_code)

    # 9.4 Scope enforcement: Read key cannot start run (403)
    res = client.post(f"/pipelines/{pipeline_id}/runs", headers={"X-API-Key": read_api_key})
    check("/pipelines/{id}/runs (Read Key Denied)", "POST", 403, res.status_code)

    # 9.5 Scope enforcement: Run key can start run (202)
    res = client.post(f"/pipelines/{pipeline_id}/runs", headers={"X-API-Key": run_api_key})
    check("/pipelines/{id}/runs (Run Key Allowed)", "POST", 202, res.status_code)

    # 9.6 List API Keys
    res = client.get("/api-keys", headers=headers_a)
    check("/api-keys", "GET", 200, res.status_code)

    # 9.7 Revoke API Key
    with open("samples/api/revoke_api_key.json") as f:
        rev_body = json.load(f)
    res = client.request("DELETE", f"/api-keys/{api_key_id}", json=rev_body, headers=headers_a)
    check("/api-keys/{id}", "DELETE", 204, res.status_code)

    # Group 10: Audit Log (Admin Only)
    # 10.1 Non-Admin Forbidden
    res = client.get("/admin/audit-log", headers=headers_a)
    check("/admin/audit-log (Non-Admin)", "GET", 403, res.status_code)

    # 10.2 Admin Allowed
    res = client.get("/admin/audit-log", headers=headers_admin)
    check("/admin/audit-log (Admin Allowed)", "GET", 200, res.status_code)

    # 10.3 CSV Export
    res = client.get("/admin/audit-log/export", headers=headers_admin)
    check("/admin/audit-log/export", "GET", 200, res.status_code)

    # Cleanup Schedule
    res = client.delete(f"/pipelines/{pipeline_id}/schedule", headers=headers_a)
    check("/pipelines/{id}/schedule", "DELETE", 204, res.status_code)

    # Note: deleting a pipeline whose versions have runs violates RESTRICT foreign key
    # so we test pipeline deletion on a fresh standalone pipeline:
    with open("samples/api/create_pipeline.json") as f:
        pipe_to_del = json.load(f)
    pipe_to_del["name"] = "pipeline-to-delete"
    res_del_create = client.post("/pipelines", json=pipe_to_del, headers=headers_a)
    del_pipe_id = res_del_create.json()["id"]

    res = client.delete(f"/pipelines/{del_pipe_id}", headers=headers_a)
    check("/pipelines/{id}", "DELETE", 204, res.status_code)

    return results


if __name__ == "__main__":
    results = run_verification()
    print("\n" + "=" * 80)
    print(f"{'Endpoint':<45} | {'Expected':<8} | {'Actual':<8} | {'Result':<6}")
    print("-" * 80)
    passed = 0
    for r in results:
        print(f"{r['endpoint']:<45} | {r['expected']:<8} | {r['actual']:<8} | {r['result']:<6}")
        if r['result'] == 'PASS':
            passed += 1
    print("=" * 80)
    print(f"Total: {len(results)} | Passed: {passed} | Failed: {len(results) - passed}")
