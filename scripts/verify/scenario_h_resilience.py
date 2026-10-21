"""Scenario H: Resilience & Orphan Run Recovery
1. Directly insert a simulated orphaned 'running' run into the database.
2. Initialize app / trigger startup recovery routine.
3. Verify the orphan is recovered:
   - status transitioned to 'failed'
   - finished_at timestamp is set
   - error message explains server restart / recovery
   - RunLog contains 'run_failed' event with restart details
"""
from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal
from app.models.entities import Pipeline, PipelineVersion, Run, RunLog, User
from app.services.run_worker import recover_orphaned_runs


def run():
    print("=== SCENARIO H: RESILIENCE & ORPHAN RECOVERY ===")

    orphan_run_id = uuid.uuid4()
    with SessionLocal() as db:
        # Get or create dummy user and pipeline
        user = db.query(User).first()
        assert user is not None, "At least one user must exist in DB"
        pipe = db.query(Pipeline).first()
        assert pipe is not None, "At least one pipeline must exist in DB"
        pv = db.query(PipelineVersion).filter(PipelineVersion.pipeline_id == pipe.id).first()
        assert pv is not None, "Pipeline version must exist"

        # Insert orphaned run with status 'running'
        orphan = Run(
            id=orphan_run_id,
            pipeline_id=pipe.id,
            pipeline_version_id=pv.id,
            owner_id=user.id,
            version=1,
            status="running",
            progress=45.0,
            started_at=datetime.now(UTC),
        )
        db.add(orphan)
        db.commit()
        print(f"  [1] Seeded orphaned run {orphan_run_id} with status='running'")

    # Trigger recovery function (which also executes in app lifespan)
    with SessionLocal() as db:
        recovered_count = recover_orphaned_runs(db)
        print(f"  [2] Recovery routine completed; recovered {recovered_count} orphan(s)")
        assert recovered_count >= 1

    # Verify run state in database
    with SessionLocal() as db:
        recovered = db.get(Run, orphan_run_id)
        assert recovered is not None
        assert recovered.status == "failed", f"Expected failed, got {recovered.status}"
        assert recovered.error is not None and "Server restarted" in recovered.error
        assert recovered.error_type == "ServerRestartError"
        assert recovered.finished_at is not None
        print(f"  [3] Verified orphan status='{recovered.status}', error='{recovered.error}'")

        # Verify persisted RunLog
        logs = db.query(RunLog).filter(RunLog.run_id == orphan_run_id).all()
        assert len(logs) >= 1
        assert logs[-1].event == "run_failed"
        assert "Server restarted" in logs[-1].message
        print("  [4] Verified RunLog entry recorded for the recovered run.")

    print("=== SCENARIO H PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
