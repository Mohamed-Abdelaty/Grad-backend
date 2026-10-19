from unittest.mock import patch

from app.services import run_worker


class FakeRun:
    def __init__(self):
        self.status = "running"
        self.started_at = None
        self.finished_at = None
        self.metrics = {}


def test_update_status_sets_started_finished_and_metrics():
    r = FakeRun()
    run_worker._update_status(r, "succeeded", metrics={"rows": 5}, logs=["ok"])
    assert r.status == "succeeded"
    assert r.started_at is not None
    assert r.finished_at is not None
    assert r.finished_at >= r.started_at
    assert r.metrics == {"rows": 5}


def test_start_worker_thread_starts_and_runs(monkeypatch):
    started = []

    class DummyThread:
        def __init__(self, target, daemon=True):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append(True)
            self.target()

    monkeypatch.setattr(run_worker.threading, "Thread", DummyThread)
    monkeypatch.setattr(run_worker, "process_pipeline", lambda db, run_id, pipeline_id: None)

    run_worker.start_worker_thread(None, "r1", "p1")
    assert started


def test_process_pipeline_handles_canceled_and_exceptions(monkeypatch):
    class Run:
        def __init__(self):
            self.status = "canceled"
            self.started_at = None
            self.finished_at = None
            self.metrics = {}

    db = object()
    with patch.object(run_worker, "_update_status") as up:
        run_worker.process_pipeline(db, "r1", "p1", run_model=Run())
        up.assert_called()
        args, kwargs = up.call_args
        assert args[1] in ("canceled", "cancelled") or getattr(args[0], "status", None) in ("canceled", "cancelled")

    class Run2:
        def __init__(self):
            self.status = "running"
            self.started_at = None
            self.finished_at = None
            self.metrics = {}

    with patch("engine.execute_pipeline", side_effect=Exception("x")):
        run_worker.process_pipeline(db, "r2", "p2", run_model=Run2())
        # should not crash


def test_get_worker_status():
    s = run_worker.get_worker_status()
    assert "running" in s
