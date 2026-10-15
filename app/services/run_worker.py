"""In-process background worker executing pipeline runs off the request path.

Limitations (documented honestly): execution is in-process via a thread
pool, so runs do NOT survive application restarts and are NOT durable
across deploys. ``POST /pipelines/{id}/runs`` returns 202 immediately with
the run id; all state transitions, logs, errors, and metrics are persisted
to the database as the worker progresses.

Concurrency: every run gets fresh plugin instances (via a new
:class:`engine.executor.Executor`), a dedicated DB session, and its own
:class:`CancellationToken`, so concurrent runs never share mutable state.
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
from engine.errors import CancellationError, EngineError
from engine.executor import CancellationToken, ExecutionEvent, Executor, validate_pipeline_dict

logger = logging.getLogger("chaosflow.worker")

_executor: ThreadPoolExecutor | None = None
_tokens: dict[str, CancellationToken] = {}
_lock = threading.Lock()
# run_id -> set of subscriber queues (WebSocket live streaming).
_subscribers: dict[str, set[queue.Queue[dict[str, Any]]]] = {}
_sub_lock = threading.Lock()


def get_pool() -> ThreadPoolExecutor:
    """Return the shared worker thread pool (created lazily)."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=settings.RUN_WORKER_THREADS, thread_name_prefix="run-worker"
        )
    return _executor


def get_token(run_id: str) -> CancellationToken | None:
    """Return the cancellation token for a live run, if any."""
    with _lock:
        return _tokens.get(run_id)


def request_stop(run_id: str) -> bool:
    """Signal cancellation for ``run_id``; False if the run is not active."""
    token = get_token(run_id)
    if token is None:
        return False
    token.cancel()
    return True


def subscribe(run_id: str) -> queue.Queue[dict[str, Any]]:
    """Subscribe to live run messages; caller must :func:`unsubscribe`."""
    q: queue.Queue[dict[str, Any]] = queue.Queue()
    with _sub_lock:
        _subscribers.setdefault(run_id, set()).add(q)
    return q


def unsubscribe(run_id: str, q: queue.Queue[dict[str, Any]]) -> None:
    """Remove a live subscription (safe on disconnects)."""
    with _sub_lock:
        bucket = _subscribers.get(run_id)
        if bucket is not None:
            bucket.discard(q)
            if not bucket:
                _subscribers.pop(run_id, None)


def publish(run_id: str, message: dict[str, Any]) -> None:
    """Publish a message to live subscribers (never raises into the worker)."""
    with _sub_lock:
        targets = list(_subscribers.get(run_id, ()))
    for q in targets:
        with contextlib.suppress(Exception):
            q.put_nowait(message)


def submit_run(run_id: str) -> None:
    """Enqueue ``run_id`` for background execution (returns immediately)."""
    token = CancellationToken()
    with _lock:
        _tokens[run_id] = token
    get_pool().submit(_execute_run, run_id, token)


def _execute_run(run_id: str, token: CancellationToken) -> None:
    # Local imports keep the engine decoupled from the API package.
    from app.db.session import SessionLocal
    from app.models.entities import PipelineVersion, Run

    db = SessionLocal()
    try:
        run = db.get(Run, UUID(run_id))
        if run is None:
            logger.error("Run not found for background execution", extra={"run_id": run_id})
            return
        version_row = db.get(PipelineVersion, run.pipeline_version_id)
        if version_row is None:
            run.status = "failed"
            run.error = "Pipeline version snapshot is missing"
            run.error_type = "VersionNotFound"
            run.finished_at = datetime.now(UTC)
            db.commit()
            return
        config_doc: dict[str, Any] = dict(version_row.config)
        try:
            doc = validate_pipeline_dict(config_doc)
        except EngineError as exc:
            run.status = "failed"
            run.error = str(exc)
            run.error_type = type(exc).__name__
            run.finished_at = datetime.now(UTC)
            db.commit()
            publish(run_id, {"type": "done", "run_id": run_id, "status": run.status})
            return

        total_nodes = len(doc.pipeline.nodes)
        finished_nodes = 0

        run.status = "running"
        run.started_at = datetime.now(UTC)
        db.commit()
        publish(run_id, {"type": "event", "event": "run_started", "run_id": run_id})
        logger.info("Run started", extra={"run_id": run_id, "pipeline_id": str(run.pipeline_id)})

        from app.models.entities import RunLog

        def persist_log(
            level: str,
            node_id: str | None,
            event: str | None,
            message: str,
            records_in: int = 0,
            records_out: int = 0,
        ) -> None:
            session = SessionLocal()
            try:
                session.add(
                    RunLog(
                        run_id=UUID(run_id),
                        level=level,
                        node_id=node_id,
                        event=event,
                        message=message,
                        records_in=records_in,
                        records_out=records_out,
                    )
                )
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

        def on_event(evt: ExecutionEvent) -> None:
            nonlocal finished_nodes
            msg = evt.message or f"{evt.type} {evt.node_id or ''}".strip()
            persist_log(
                "INFO" if evt.type not in ("node_failed",) else "ERROR",
                evt.node_id,
                evt.type,
                msg,
                evt.records_in,
                evt.records_out,
            )
            if evt.type == "node_finished":
                finished_nodes += 1
            progress = (finished_nodes / total_nodes * 100.0) if total_nodes else 100.0
            session = SessionLocal()
            try:
                row = session.get(Run, UUID(run_id))
                if row is not None:
                    row.progress = progress
                    if evt.type == "batch_processed":
                        row.records_in = (row.records_in or 0) + evt.records_in
                        row.records_out = (row.records_out or 0) + evt.records_out
                    session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()
            publish(
                run_id,
                {
                    "type": "event",
                    "event": evt.type,
                    "run_id": run_id,
                    "node_id": evt.node_id,
                    "message": msg,
                    "batch_index": evt.batch_index,
                    "records_in": evt.records_in,
                    "records_out": evt.records_out,
                    "attempt": evt.attempt,
                    "timestamp": evt.timestamp,
                },
            )
            publish(
                run_id,
                {
                    "type": "progress",
                    "run_id": run_id,
                    "progress": progress,
                    "finished_nodes": finished_nodes,
                    "total_nodes": total_nodes,
                    "note": "graph-progress estimate (nodes finished / total nodes), not record-level completion",
                },
            )

        executor = Executor(
            doc.pipeline,
            event_callback=on_event,
            batch_size=settings.ENGINE_BATCH_SIZE,
            max_backoff_seconds=settings.ENGINE_MAX_BACKOFF_SECONDS,
        )
        try:
            result = executor.run(token)
        except CancellationError:
            run.status = "cancelled"
            run.finished_at = datetime.now(UTC)
            db.commit()
            persist_log("WARNING", None, "cancelled", "Run cancelled by user")
            publish(run_id, {"type": "done", "run_id": run_id, "status": "cancelled"})
            logger.info("Run cancelled", extra={"run_id": run_id})
            return
        except EngineError as exc:
            node_id = getattr(exc, "node_id", None)
            run.status = "failed"
            run.error = str(exc)[:2000]
            run.error_type = getattr(exc, "error_type", None) or type(exc).__name__
            run.failed_node_id = node_id
            run.finished_at = datetime.now(UTC)
            db.commit()
            publish(
                run_id,
                {"type": "done", "run_id": run_id, "status": "failed", "error": str(exc)[:1000]},
            )
            logger.error("Run failed: %s", exc, extra={"run_id": run_id})
            return

        metrics_payload = {
            nid: {
                "records_in": m.records_in,
                "records_out": m.records_out,
                "duration_seconds": m.duration_seconds,
                "error_count": m.error_count,
                "attempts": m.attempts,
            }
            for nid, m in result.metrics.items()
        }
        # Final counters are sink/source totals (not the cumulative live
        # batch counters used for progress). Live `records_in/out` sum every
        # batch event across nodes; on completion we report what the run
        # actually read (sources) and wrote (sinks).
        source_ids = {n.id for n in doc.pipeline.nodes if n.type == "source"}
        sink_ids = {n.id for n in doc.pipeline.nodes if n.type == "sink"}
        run.records_in = sum(
            result.metrics[nid].records_out for nid in source_ids if nid in result.metrics
        )
        run.records_out = sum(
            result.metrics[nid].records_out for nid in sink_ids if nid in result.metrics
        )
        run.status = "succeeded"
        run.progress = 100.0
        run.run_metrics = metrics_payload
        run.finished_at = datetime.now(UTC)
        db.commit()
        publish(run_id, {"type": "done", "run_id": run_id, "status": "succeeded"})
        logger.info("Run succeeded", extra={"run_id": run_id})
    except Exception as exc:  # never crash the worker thread silently
        logger.exception("Worker crashed for run %s: %s", run_id, exc)
        try:
            from app.db.session import SessionLocal as _SL
            from app.models.entities import Run as _Run

            session = _SL()
            try:
                row = session.get(_Run, UUID(run_id))
                if row is not None and row.status not in ("succeeded", "failed", "cancelled"):
                    row.status = "failed"
                    row.error = f"Worker error: {exc}"[:2000]
                    row.error_type = "WorkerError"
                    row.finished_at = datetime.now(UTC)
                    session.commit()
            finally:
                session.close()
        except Exception:
            pass
        publish(run_id, {"type": "done", "run_id": run_id, "status": "failed"})
    finally:
        with _lock:
            _tokens.pop(run_id, None)
        db.close()
