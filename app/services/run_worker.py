"""Background run worker: executes pipeline runs in worker threads, handles cancellation,
persists run logs, updates progress and metrics, and broadcasts live events.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import Run, RunLog
from engine.config import PipelineDocument
from engine.errors import CancellationError, NodeExecutionError
from engine.executor import CancellationToken, ExecutionEvent, Executor

logger = logging.getLogger("chaosflow.worker")

_lock = threading.Lock()
_sub_lock = threading.Lock()
_tokens: dict[str, CancellationToken] = {}
_subscribers: dict[str, list[queue.Queue]] = {}
_executor: ThreadPoolExecutor | None = None


def get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=settings.RUN_WORKER_THREADS,
                thread_name_prefix="chaosflow-worker",
            )
        return _executor


def shutdown_worker() -> None:
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None


def get_worker_status() -> dict[str, Any]:
    with _lock:
        return {
            "running": True,
            "max_workers": settings.RUN_WORKER_THREADS,
            "active_tokens": len(_tokens),
        }


def subscribe(run_id: str) -> queue.Queue:
    """Subscribe to real-time execution events for run_id."""
    q: queue.Queue = queue.Queue()
    with _sub_lock:
        _subscribers.setdefault(str(run_id), []).append(q)
    return q


def unsubscribe(run_id: str, q: queue.Queue) -> None:
    """Unsubscribe from real-time execution events for run_id."""
    with _sub_lock:
        subs = _subscribers.get(str(run_id))
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                _subscribers.pop(str(run_id), None)


def _broadcast(run_id: str, message: dict[str, Any]) -> None:
    """Send a message to all active WebSocket subscriber queues for run_id."""
    with _sub_lock:
        subs = list(_subscribers.get(str(run_id), []))
    for q in subs:
        with contextlib.suppress(Exception):
            q.put_nowait(message)


def request_stop(run_id: str) -> bool:
    """Request cooperative cancellation of an active run."""
    with _lock:
        token = _tokens.get(str(run_id))
    if token is not None:
        token.cancel()
        return True
    return False


def _update_status(run: Any, status: str, metrics: Any = None, logs: Any = None) -> None:
    """Helper used in test suites to simulate run status updates."""
    run.status = status
    now = datetime.now(UTC)
    if getattr(run, "started_at", None) is None:
        run.started_at = now
    if getattr(run, "finished_at", None) is None:
        run.finished_at = now
    if metrics is not None:
        if hasattr(run, "run_metrics"):
            run.run_metrics = metrics
        if hasattr(run, "metrics"):
            run.metrics = metrics


def start_worker_thread(db: Any, run_id: str, pipeline_id: str) -> None:
    """Legacy helper for testing worker thread execution."""
    def target() -> None:
        process_pipeline(db, run_id, pipeline_id)

    t = threading.Thread(target=target, daemon=True)
    t.start()


def process_pipeline(db: Any, run_id: str, pipeline_id: str, run_model: Any = None) -> None:
    """Legacy test helper for processing a pipeline directly."""
    session = db if db is not None else SessionLocal()
    try:
        run = run_model
        if run is None and session is not None:
            with contextlib.suppress(Exception):
                run = session.get(Run, UUID(str(run_id)))

        if getattr(run, "status", "") in ("canceled", "cancelled"):
            _update_status(run, run.status)
            return

        try:
            from engine import execute_pipeline
            execute_pipeline(db, run_id, pipeline_id)
            if run is not None:
                _update_status(run, "succeeded", metrics={})
        except Exception:
            if run is not None:
                _update_status(run, "failed", metrics={})
    finally:
        if session is not None and session is not db:
            with contextlib.suppress(Exception):
                session.close()


def recover_orphaned_runs(db: Any) -> int:
    """Mark any orphaned 'queued' or 'running' runs as failed on startup."""
    try:
        orphans = db.query(Run).filter(Run.status.in_(["queued", "running"])).all()
        now = datetime.now(UTC)
        for r in orphans:
            r.status = "failed"
            r.error = "Server restarted while run was in progress"
            r.error_type = "ServerRestartError"
            r.finished_at = now
            db.add(
                RunLog(
                    run_id=r.id,
                    level="ERROR",
                    event="run_failed",
                    message="Server restarted while run was in progress",
                    timestamp=now,
                )
            )
        db.commit()
        if orphans:
            logger.warning("Recovered %d orphaned runs on startup", len(orphans))
        return len(orphans)
    except Exception as exc:
        logger.error("Failed recovering orphaned runs: %s", exc)
        return 0


def submit_run(run_id: str) -> None:
    """Submit a run to execute in the background worker thread pool."""
    token = CancellationToken()
    with _lock:
        _tokens[str(run_id)] = token
    pool = get_executor()
    pool.submit(_execute_run, str(run_id), token)


def _execute_run(run_id: str, token: CancellationToken) -> None:
    """Worker thread execution target for a single pipeline run."""
    run_uuid = UUID(run_id)
    db = SessionLocal()
    try:
        run = db.get(Run, run_uuid)
        if run is None:
            logger.error("Run %s not found in DB", run_id)
            return

        if run.status in ("cancelled", "failed", "succeeded"):
            return

        # Read pipeline version document
        current_ver = next(
            (v for v in run.pipeline.versions if v.id == run.pipeline_version_id), None
        )
        if current_ver is None:
            # Fallback by version number
            current_ver = next(
                (v for v in run.pipeline.versions if v.version == run.version), None
            )

        if current_ver is None or not current_ver.config:
            run.status = "failed"
            run.error = "Pipeline configuration version not found"
            run.error_type = "ConfigurationError"
            run.finished_at = datetime.now(UTC)
            db.commit()
            _broadcast(run_id, {"type": "done", "run_id": run_id, "status": "failed"})
            try:
                from app.services.notification import emit_run_failed
                pipe_name = run.pipeline.name if run.pipeline else "Pipeline"
                emit_run_failed(db, pipe_name, run.id, run.error, run.owner_id)
            except Exception as notify_err:
                logger.warning("Failed to emit run_failed notification: %s", notify_err)
            return

        doc = PipelineDocument.model_validate(current_ver.config)
        pipeline_cfg = doc.pipeline
        total_nodes = len(pipeline_cfg.nodes)
        finished_nodes: set[str] = set()

        run.status = "running"
        run.started_at = datetime.now(UTC)
        run.progress = 0.0
        db.commit()

        def on_event(event: ExecutionEvent) -> None:
            now = datetime.now(UTC)
            log_entry = RunLog(
                run_id=run.id,
                timestamp=now,
                level="ERROR" if event.type in ("node_failed", "run_failed") else "INFO",
                node_id=event.node_id,
                event=event.type,
                message=event.message,
                records_in=event.records_in,
                records_out=event.records_out,
            )
            db.add(log_entry)

            if event.type == "node_finished" and event.node_id:
                finished_nodes.add(event.node_id)
                # Sink or final transform records
                node_obj = pipeline_cfg.node_map().get(event.node_id)
                if node_obj and node_obj.type == "sink":
                    run.records_out = max(run.records_out or 0, event.records_out)
                elif node_obj and node_obj.type == "source":
                    run.records_in = max(run.records_in or 0, event.records_out)
                elif not any(n.type == "sink" for n in pipeline_cfg.nodes):
                    run.records_out = max(run.records_out or 0, event.records_out)

            pct = round((len(finished_nodes) / max(1, total_nodes)) * 100.0, 1)
            run.progress = min(100.0, pct)
            with contextlib.suppress(Exception):
                db.commit()

            # Broadcast live progress & event
            _broadcast(
                run_id,
                {
                    "type": "event",
                    "event": event.type,
                    "node_id": event.node_id,
                    "message": event.message,
                    "records_in": event.records_in,
                    "records_out": event.records_out,
                },
            )
            _broadcast(
                run_id,
                {
                    "type": "progress",
                    "progress": run.progress,
                    "note": "estimate",
                    "records_in": run.records_in or 0,
                    "records_out": run.records_out or 0,
                },
            )

        executor = Executor(
            pipeline_cfg,
            event_callback=on_event,
            batch_size=settings.ENGINE_BATCH_SIZE,
            max_backoff_seconds=settings.ENGINE_MAX_BACKOFF_SECONDS,
        )

        def _save_metrics(raw_metrics: dict[str, Any]) -> None:
            metrics_dict: dict[str, Any] = {}
            total_in = 0
            total_out = 0
            for nid, m in raw_metrics.items():
                metrics_dict[nid] = {
                    "records_in": m.records_in,
                    "records_out": m.records_out,
                    "duration_seconds": m.duration_seconds,
                    "error_count": m.error_count,
                    "attempts": m.attempts,
                }
                node_obj = pipeline_cfg.node_map().get(nid)
                if node_obj and node_obj.type == "source":
                    total_in += m.records_out
                if node_obj and node_obj.type == "sink":
                    total_out += m.records_out
            if metrics_dict:
                run.run_metrics = metrics_dict
                run.records_in = total_in
                run.records_out = total_out if any(n.type == "sink" for n in pipeline_cfg.nodes) else (
                    list(raw_metrics.values())[-1].records_out if raw_metrics else 0
                )

        try:
            result = executor.run(cancellation=token)
            run.status = "succeeded"
            run.progress = 100.0
            run.finished_at = datetime.now(UTC)
            _save_metrics(result.metrics)
            db.commit()
            _broadcast(run_id, {"type": "done", "run_id": run_id, "status": "succeeded"})
            try:
                from app.services.notification import emit_run_succeeded
                pipe_name = run.pipeline.name if run.pipeline else "Pipeline"
                emit_run_succeeded(db, pipe_name, run.id, run.owner_id)
            except Exception as notify_err:
                logger.warning("Failed to emit run_succeeded notification: %s", notify_err)

        except CancellationError:
            run.status = "cancelled"
            run.finished_at = datetime.now(UTC)
            _save_metrics(executor.last_metrics)
            db.commit()
            _broadcast(run_id, {"type": "done", "run_id": run_id, "status": "cancelled"})

        except NodeExecutionError as exc:
            run.status = "failed"
            run.error = str(exc)
            run.error_type = exc.error_type
            run.failed_node_id = exc.node_id
            run.finished_at = datetime.now(UTC)
            _save_metrics(getattr(exc, "metrics", executor.last_metrics))
            db.commit()
            _broadcast(run_id, {"type": "done", "run_id": run_id, "status": "failed"})
            try:
                from app.services.notification import emit_run_failed
                pipe_name = run.pipeline.name if run.pipeline else "Pipeline"
                emit_run_failed(db, pipe_name, run.id, run.error or "Run failed", run.owner_id)
            except Exception as notify_err:
                logger.warning("Failed to emit run_failed notification: %s", notify_err)

        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            run.error_type = type(exc).__name__
            run.finished_at = datetime.now(UTC)
            _save_metrics(getattr(executor, "last_metrics", {}))
            db.commit()
            _broadcast(run_id, {"type": "done", "run_id": run_id, "status": "failed"})
            try:
                from app.services.notification import emit_run_failed
                pipe_name = run.pipeline.name if run.pipeline else "Pipeline"
                emit_run_failed(db, pipe_name, run.id, run.error or "Run failed", run.owner_id)
            except Exception as notify_err:
                logger.warning("Failed to emit run_failed notification: %s", notify_err)

    except Exception as exc:
        logger.error("Fatal error executing run %s: %s", run_id, exc)
    finally:
        with _lock:
            _tokens.pop(str(run_id), None)
        with contextlib.suppress(Exception):
            db.close()
