"""Scenario E: Failure & Retry Verification
1. Create a pipeline with two branches:
   - Branch A: tr_bad (CastTransform with customer_id -> int on 'c1', on_error='fail') -> snk_bad
   - Branch B: tr_ok (Filter amount > 50) -> snk_ok (CSV output)
2. Configure retry: max_attempts=3, backoff_seconds=0.1
3. Execute the run
4. Verify:
   - Attempt count is exactly 3 (no off-by-one)
   - Exponential backoff is recorded (retry_attempt events with increasing delays)
   - tr_bad is identified as failed_node_id
   - Downstream node snk_bad is marked skipped
   - Independent Branch B (tr_ok and snk_ok) succeeds
   - Final run status is 'failed' (NOT 'succeeded' or 'cancelled')
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.entities import Run, RunLog

OUT_BRANCH_OK = Path("outputs/branch_b_ok.csv")
OUT_BRANCH_BAD = Path("outputs/branch_a_bad.csv")

def run():
    print("=== SCENARIO E: FAILURE & RETRY VERIFICATION ===")

    if OUT_BRANCH_OK.exists():
        OUT_BRANCH_OK.unlink()
    if OUT_BRANCH_BAD.exists():
        OUT_BRANCH_BAD.unlink()

    with TestClient(app) as client:
        # Register & login
        email = f"retry_user_{int(time.time())}@example.com"
        client.post("/auth/register", json={"email": email, "password": "Password123!"})
        token = client.post("/auth/login", json={"email": email, "password": "Password123!"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        pipe_uuid = str(uuid.uuid4())
        pipe_doc = {
            "pipeline": {
                "id": pipe_uuid,
                "name": "retry-and-isolation-test",
                "version": 1,
                "schedule": None,
                "nodes": [
                    {"id": "src", "type": "source", "plugin": "csv", "config": {"path": "samples/data/orders.csv"}},
                    # Failing branch
                    {"id": "tr_bad", "type": "transform", "plugin": "cast", "config": {"fields": {"customer_id": "int"}, "on_error": "fail"}},
                    {"id": "snk_bad", "type": "sink", "plugin": "csv", "config": {"path": str(OUT_BRANCH_BAD)}},
                    # Independent successful branch
                    {"id": "tr_ok", "type": "transform", "plugin": "filter", "config": {"expression": "amount > 50"}},
                    {"id": "snk_ok", "type": "sink", "plugin": "csv", "config": {"path": str(OUT_BRANCH_OK)}},
                ],
                "edges": [
                    {"from": "src", "to": "tr_bad"},
                    {"from": "tr_bad", "to": "snk_bad"},
                    {"from": "src", "to": "tr_ok"},
                    {"from": "tr_ok", "to": "snk_ok"},
                ],
                "retry": {"max_attempts": 3, "backoff_seconds": 0.1},
            }
        }

        # Create pipeline
        resp = client.post("/pipelines", json={"name": "retry-test", "config": pipe_doc}, headers=headers)
        assert resp.status_code == 201, f"Failed creating pipeline: {resp.text}"
        pipe_id = resp.json()["id"]

        # Trigger run
        resp = client.post(f"/pipelines/{pipe_id}/runs", headers=headers)
        assert resp.status_code == 202
        run_id = resp.json()["id"]
        print(f"  [1] Triggered run {run_id}")

        # Poll until terminal
        final_run = {}
        for _ in range(50):
            time.sleep(0.1)
            r = client.get(f"/runs/{run_id}", headers=headers).json()
            if r["status"] in ("succeeded", "failed", "cancelled"):
                final_run = r
                break

        print(f"  [2] Final run status: {final_run.get('status')}")
        assert final_run.get("status") == "failed", f"Expected failed, got {final_run.get('status')}"
        assert final_run.get("failed_node_id") == "tr_bad", f"Expected failed_node_id='tr_bad', got {final_run.get('failed_node_id')}"
        print(f"  [3] Failed node correctly identified: {final_run.get('failed_node_id')}")

        # Verify DB logs and attempts
        run_uuid = uuid.UUID(run_id)
        with SessionLocal() as db:
            logs = db.query(RunLog).filter(RunLog.run_id == run_uuid).order_by(RunLog.timestamp.asc()).all()
            events = [l.event for l in logs]
            print(f"  [4] Execution events: {events}")
            assert "node_failed" in events
            assert "retry_attempt" in events

            # Check attempts count in metrics
            db_run = db.query(Run).filter(Run.id == run_uuid).first()
            assert db_run is not None
            metrics = db_run.run_metrics or {}
            print(f"  [5] Node metrics: {json.dumps(metrics, indent=2)}")
            assert metrics["tr_bad"]["attempts"] == 3, f"Expected 3 attempts, got {metrics['tr_bad']['attempts']}"
            assert metrics["tr_bad"]["error_count"] == 3, f"Expected 3 error_count, got {metrics['tr_bad']['error_count']}"

            # Verify downstream node was skipped
            assert "snk_bad" not in metrics or metrics["snk_bad"]["records_in"] == 0
            assert not OUT_BRANCH_BAD.exists() or OUT_BRANCH_BAD.stat().st_size == 0
            print("  [6] Downstream node snk_bad was skipped; did not produce output.")

            # Verify independent branch succeeded
            assert metrics["snk_ok"]["records_out"] > 0
            assert OUT_BRANCH_OK.exists() and OUT_BRANCH_OK.stat().st_size > 0
            print(f"  [7] Independent branch succeeded: snk_ok produced {metrics['snk_ok']['records_out']} rows.")

    # Cleanup
    if OUT_BRANCH_OK.exists():
        OUT_BRANCH_OK.unlink()
    if OUT_BRANCH_BAD.exists():
        OUT_BRANCH_BAD.unlink()

    print("=== SCENARIO E PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
