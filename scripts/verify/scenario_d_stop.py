"""Scenario D: Mid-Run Cancellation (Stop)
1. Generate large dataset (200,000 rows)
2. Submit pipeline run
3. Issue POST /runs/{id}/stop while running
4. Verify run reaches 'cancelled' status (NOT 'failed')
5. Verify sinks cleanly closed without file corruption
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient

from app.main import app

LARGE_CSV = Path("samples/data/large_orders.csv")
OUT_LARGE = Path("outputs/large_clean.csv")

def generate_large_csv(count: int = 200_000):
    LARGE_CSV.parent.mkdir(parents=True, exist_ok=True)
    print(f"  Generating {count:,} rows into {LARGE_CSV}...")
    with open(LARGE_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "customer_id", "amount", "status"])
        for i in range(1, count + 1):
            writer.writerow([f"ord_{i}", f"cust_{i % 500}", (i * 7) % 300, "COMPLETED"])
    print("  Generation complete.")

def run():
    print("=== SCENARIO D: MID-RUN CANCELLATION ===")

    if OUT_LARGE.exists():
        OUT_LARGE.unlink()

    generate_large_csv(200_000)

    with TestClient(app) as client:
        # 1. Register & login
        email = f"stop_user_{int(time.time())}@example.com"
        client.post("/auth/register", json={"email": email, "password": "Password123!"})
        token = client.post("/auth/login", json={"email": email, "password": "Password123!"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Create pipeline
        import uuid
        pipe_uuid = str(uuid.uuid4())
        pipe_doc = {
            "pipeline": {
                "id": pipe_uuid,
                "name": "large-cancel-test",
                "version": 1,
                "schedule": None,
                "nodes": [
                    {"id": "src", "type": "source", "plugin": "csv", "config": {"path": str(LARGE_CSV)}},
                    {"id": "flt", "type": "transform", "plugin": "filter", "config": {"expression": "amount > 50"}},
                    {"id": "snk", "type": "sink", "plugin": "csv", "config": {"path": str(OUT_LARGE)}},
                ],
                "edges": [
                    {"from": "src", "to": "flt"},
                    {"from": "flt", "to": "snk"},
                ],
                "retry": {"max_attempts": 1, "backoff_seconds": 1},
            }
        }
        resp = client.post("/pipelines", json={"name": "large-cancel-test", "config": pipe_doc}, headers=headers)
        assert resp.status_code == 201, f"Failed creating pipeline: {resp.text}"
        pipe_id = resp.json()["id"]

        # 3. Trigger run
        resp = client.post(f"/pipelines/{pipe_id}/runs", headers=headers)
        assert resp.status_code == 202
        run_id = resp.json()["id"]
        print(f"  [1] Triggered 200k-row run {run_id}")

        # 4. Wait brief interval so execution begins, then request stop
        time.sleep(0.15)
        stop_resp = client.post(f"/runs/{run_id}/stop", headers=headers)
        print(f"  [2] POST /runs/{run_id}/stop response: {stop_resp.status_code}")
        assert stop_resp.status_code in (200, 202, 409)

        # 5. Poll for terminal state
        final_status = None
        for _ in range(50):
            time.sleep(0.1)
            r = client.get(f"/runs/{run_id}", headers=headers).json()
            if r["status"] in ("succeeded", "failed", "cancelled"):
                final_status = r["status"]
                break

        print(f"  [3] Run terminal status: {final_status}")
        assert final_status == "cancelled", f"Expected 'cancelled', got '{final_status}'"
        print("  [PASS] Run cancelled cooperatively.")

        # 6. Verify sink file was cleanly closed
        if OUT_LARGE.exists():
            with open(OUT_LARGE, encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                assert header is not None, "Sink file is corrupt / empty header"
                written_rows = list(reader)
                print(f"  [4] Verified sink file is valid CSV with {len(written_rows)} flushed rows before stop")

    # Cleanup large file
    if LARGE_CSV.exists():
        LARGE_CSV.unlink()
    if OUT_LARGE.exists():
        OUT_LARGE.unlink()

    print("=== SCENARIO D PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
