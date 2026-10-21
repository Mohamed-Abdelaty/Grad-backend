"""Scenario B: API Happy Path
1. Register a new user
2. Login and retrieve JWT access token
3. POST /pipelines with samples/pipeline.json
4. POST /pipelines/{id}/runs to trigger background run
5. Poll GET /runs/{id} until terminal status 'success'
6. Verify outputs/orders_clean.csv has 10 rows
7. Verify DB RunLog has persisted node_started, batch_processed, node_finished entries
8. Verify Run.metrics match expected counts (src_1: 15/15, tr_1: 15/10, snk_1: 10/10)
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.entities import Run, RunLog


def run():
    print("=== SCENARIO B: API HAPPY PATH ===")

    out_clean = Path("outputs/orders_clean.csv")
    if out_clean.exists():
        out_clean.unlink()

    with TestClient(app) as client:
        # 1. Register
        user_email = f"scenario_b_{int(time.time())}@example.com"
        user_pass = "SecurePass123!"
        resp = client.post("/auth/register", json={
            "email": user_email,
            "password": user_pass,
        })
        assert resp.status_code == 201, f"Register failed: {resp.text}"
        user_data = resp.json()
        print(f"  [1] Registered user {user_email} (id: {user_data['id']})")

        # 2. Login
        resp = client.post("/auth/login", json={
            "email": user_email,
            "password": user_pass,
        })
        assert resp.status_code == 200, f"Login failed: {resp.text}"
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("  [2] Logged in successfully, acquired JWT")

        # 3. Create pipeline
        pipe_doc = json.loads(Path("samples/pipeline.json").read_text(encoding="utf-8"))
        pipe_payload = {
            "name": pipe_doc["pipeline"]["name"],
            "config": pipe_doc,
        }
        resp = client.post("/pipelines", json=pipe_payload, headers=headers)
        assert resp.status_code == 201, f"Create pipeline failed: {resp.text}"
        pipe_data = resp.json()
        pipeline_id = pipe_data["id"]
        print(f"  [3] Created pipeline '{pipe_data['name']}' (id: {pipeline_id})")

        # 4. Trigger run
        resp = client.post(f"/pipelines/{pipeline_id}/runs", headers=headers)
        assert resp.status_code == 202, f"Trigger run failed: {resp.text}"
        run_data = resp.json()
        run_id = run_data["id"]
        print(f"  [4] Triggered run (id: {run_id}), initial status: {run_data['status']}")

        # 5. Poll GET /runs/{id}
        terminal = False
        final_run_data = {}
        for _ in range(40):
            time.sleep(0.2)
            r = client.get(f"/runs/{run_id}", headers=headers)
            assert r.status_code == 200, f"Poll run failed: {r.text}"
            final_run_data = r.json()
            if final_run_data["status"] in ("succeeded", "failed", "cancelled"):
                terminal = True
                break

        assert terminal, f"Run did not terminate in time: {final_run_data}"
        assert final_run_data["status"] == "succeeded", f"Run failed with status: {final_run_data.get('status')}"
        print(f"  [5] Run completed with status: {final_run_data['status']}")

        # 6. Verify output CSV
        assert out_clean.exists(), f"Output file {out_clean} does not exist"
        with open(out_clean, encoding="utf-8") as f:
            reader = list(csv.reader(f))
            header = reader[0]
            rows = reader[1:]
            assert len(rows) == 10, f"Expected 10 rows in CSV, got {len(rows)}"
        print(f"  [6] Verified {len(rows)} clean rows in {out_clean}")

        # 7. Check DB RunLog
        import uuid
        run_uuid = uuid.UUID(run_id) if isinstance(run_id, str) else run_id
        with SessionLocal() as db:
            logs = db.query(RunLog).filter(RunLog.run_id == run_uuid).order_by(RunLog.timestamp.asc()).all()
            events = [l.event for l in logs]
            print(f"  [7] Persisted {len(logs)} RunLog entries. Events: {set(events)}")
            assert "node_started" in events
            assert "batch_processed" in events
            assert "node_finished" in events

            # 8. Check metrics
            db_run = db.query(Run).filter(Run.id == run_uuid).first()
            assert db_run is not None
            metrics = db_run.run_metrics or {}
            print(f"  [8] Run metrics in DB: {json.dumps(metrics, indent=2)}")
            assert "src_1" in metrics and metrics["src_1"]["records_out"] == 15
            assert "tr_1" in metrics and metrics["tr_1"]["records_out"] == 10
            assert "snk_1" in metrics and metrics["snk_1"]["records_out"] == 10

    print("=== SCENARIO B PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
