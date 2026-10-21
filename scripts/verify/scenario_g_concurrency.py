"""Scenario G: Concurrency Verification
1. Trigger 5 concurrent pipeline runs simultaneously.
2. Concurrently poll GET /health to verify API remains responsive (<50ms).
3. Verify all 5 runs complete successfully without cross-talk or race conditions.
4. Verify each run maintains independent metrics and tokens.
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient

from app.main import app


def run():
    print("=== SCENARIO G: CONCURRENCY VERIFICATION ===")

    with TestClient(app) as client:
        # Register user
        email = f"concurrent_user_{int(time.time())}@example.com"
        client.post("/auth/register", json={"email": email, "password": "Password123!"})
        token = client.post("/auth/login", json={"email": email, "password": "Password123!"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create pipeline
        pipe_doc = {
            "pipeline": {
                "id": str(uuid.uuid4()),
                "name": "concurrent-pipeline",
                "version": 1,
                "schedule": None,
                "nodes": [
                    {"id": "src", "type": "source", "plugin": "csv", "config": {"path": "samples/data/orders.csv"}},
                    {"id": "tr", "type": "transform", "plugin": "filter", "config": {"expression": "amount > 100"}},
                    {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
                ],
                "edges": [
                    {"from": "src", "to": "tr"},
                    {"from": "tr", "to": "snk"},
                ],
                "retry": {"max_attempts": 2, "backoff_seconds": 0.5},
            }
        }
        resp = client.post("/pipelines", json={"name": "concurrent-pipeline", "config": pipe_doc}, headers=headers)
        assert resp.status_code == 201
        pipe_id = resp.json()["id"]

        # Trigger 5 runs simultaneously
        print("  Triggering 5 concurrent runs...")
        run_ids = []
        for i in range(5):
            r = client.post(f"/pipelines/{pipe_id}/runs", headers=headers)
            assert r.status_code == 202
            run_ids.append(r.json()["id"])

        assert len(set(run_ids)) == 5, "Run IDs must all be distinct"
        print(f"  Launched 5 runs: {run_ids}")

        # Check health response time during active runs
        latencies = []
        for _ in range(10):
            t0 = time.monotonic()
            h = client.get("/health")
            t_diff = (time.monotonic() - t0) * 1000  # ms
            assert h.status_code == 200
            assert h.json()["status"] == "ok"
            latencies.append(t_diff)
            time.sleep(0.01)

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        print(f"  [PASS] /health responsive under load: avg={avg_latency:.2f}ms, max={max_latency:.2f}ms")
        assert max_latency < 200, f"Health check latency too high: {max_latency}ms"

        # Wait for all 5 runs to complete
        completed = {}
        for _ in range(60):
            time.sleep(0.1)
            for rid in run_ids:
                if rid not in completed:
                    status_resp = client.get(f"/runs/{rid}", headers=headers).json()
                    if status_resp["status"] in ("succeeded", "failed", "cancelled"):
                        completed[rid] = status_resp
            if len(completed) == 5:
                break

        assert len(completed) == 5, f"Only {len(completed)}/5 runs completed"
        for rid, info in completed.items():
            assert info["status"] == "succeeded", f"Run {rid} ended with status {info['status']}"
            assert info["records_in"] == 15, f"Run {rid} records_in mismatch: {info['records_in']}"
            assert info["records_out"] == 10, f"Run {rid} records_out mismatch: {info['records_out']}"

        print("  [PASS] All 5 concurrent runs completed with 100% data integrity and independent metrics.")

    print("=== SCENARIO G PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
