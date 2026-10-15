"""Real-time run streaming: ``WS /ws/runs/{id}``.

Message schema (JSON, one object per frame):
- ``{"type": "history", "run_id", "status", "logs": [...]}`` — replay on connect.
- ``{"type": "event", "event", "run_id", "node_id", "message", "batch_index",
  "records_in", "records_out", "attempt", "timestamp"}`` — live execution events.
- ``{"type": "progress", "run_id", "progress", "finished_nodes", "total_nodes",
  "note"}`` — graph-progress ESTIMATE (nodes finished / total nodes),
  explicitly not record-level completion.
- ``{"type": "done", "run_id", "status", "error"?}`` — terminal state.
- ``{"type": "error", "message"}`` — auth/access failures (then close).

Auth: ``?token=<JWT access token>`` query param (browsers cannot set WS
headers) or the ``Authorization: Bearer`` header. Ownership is enforced:
users only receive their own runs. Disconnects only drop the subscription;
the worker keeps persisting logs, and reconnects replay history.
"""

from __future__ import annotations

import contextlib
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from app.core.security import TokenError, decode_token
from app.db.session import SessionLocal
from app.models.entities import TERMINAL_STATUSES, Run, RunLog, User
from app.services import run_worker

logger = logging.getLogger("chaosflow.ws")
router = APIRouter()


def _auth_user(token: str | None) -> User | None:
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError:
        return None
    db: Session = SessionLocal()
    try:
        user = db.get(User, uuid.UUID(str(payload["sub"])))
        if user is None or not user.is_active:
            return None
        db.expunge(user)
        return user
    except (ValueError, AttributeError):
        return None
    finally:
        db.close()


@router.websocket("/ws/runs/{run_id}")
async def stream_run(
    websocket: WebSocket, run_id: uuid.UUID, token: str | None = Query(default=None)
) -> None:
    """Stream live execution events, progress, and counters for a run."""
    header_token = None
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        header_token = auth_header[7:]
    user = _auth_user(token or header_token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    db: Session = SessionLocal()
    try:
        run = db.get(Run, run_id)
        if run is None or (user.role != "admin" and run.owner_id != user.id):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        logs = (
            db.query(RunLog)
            .filter(RunLog.run_id == run.id)
            .order_by(RunLog.timestamp.asc())
            .limit(500)
            .all()
        )
        history = [
            {
                "event": r.event,
                "node_id": r.node_id,
                "message": r.message,
                "records_in": r.records_in,
                "records_out": r.records_out,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in logs
        ]
        current_status = run.status
    finally:
        db.close()

    await websocket.accept()
    await websocket.send_json(
        {"type": "history", "run_id": str(run_id), "status": current_status, "logs": history}
    )
    if current_status in TERMINAL_STATUSES:
        await websocket.send_json({"type": "done", "run_id": str(run_id), "status": current_status})
        await websocket.close()
        return

    inbox = run_worker.subscribe(str(run_id))
    try:
        while True:
            try:
                message = inbox.get(timeout=1.0)
            except Exception:
                # Periodic liveness check: exit cleanly once terminal + drained.
                session: Session = SessionLocal()
                try:
                    row = session.get(Run, run_id)
                    terminal = row is not None and row.status in TERMINAL_STATUSES
                finally:
                    session.close()
                if terminal and inbox.empty():
                    await websocket.send_json(
                        {
                            "type": "done",
                            "run_id": str(run_id),
                            "status": row.status if row else "unknown",
                        }
                    )
                    break
                continue
            try:
                await websocket.send_json(message)
            except (WebSocketDisconnect, RuntimeError):
                break
            if isinstance(message, dict) and message.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    finally:
        run_worker.unsubscribe(str(run_id), inbox)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
