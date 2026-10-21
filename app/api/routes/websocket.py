"""Real-time run streaming over WebSockets: logs, progress, and terminal status."""

from __future__ import annotations

import asyncio
import contextlib
import queue
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.security import TokenError, decode_token
from app.db.session import SessionLocal
from app.models.entities import TERMINAL_STATUSES, Run, RunLog, User
from app.schemas.run import RunLogResponse
from app.services import run_worker

router = APIRouter()


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_personal_message(self, message: Any, websocket: WebSocket) -> None:
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, message: Any) -> None:
        for conn in list(self.active_connections):
            try:
                await conn.send_json(message)
            except Exception:
                self.disconnect(conn)


manager = ConnectionManager()


@router.websocket("/ws/runs/{run_id}")
async def run_websocket_stream(
    websocket: WebSocket,
    run_id: uuid.UUID,
    token: str | None = Query(default=None),
) -> None:
    """Real-time run event streaming: history -> live progress/events -> done."""
    # 1. Authenticate token
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        payload = decode_token(token, expected_type="access")
        user_id = uuid.UUID(str(payload["sub"]))
    except (TokenError, ValueError, KeyError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 2. Check user and ownership in database
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        run = db.get(Run, run_id)
        if run is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # Ownership authorization: only admin or the run's owner
        if user.role != "admin" and run.owner_id != user.id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # Accept the connection
        await websocket.accept()

        # 3. Send historical logs
        logs = (
            db.query(RunLog)
            .filter(RunLog.run_id == run.id)
            .order_by(RunLog.timestamp.asc())
            .all()
        )
        logs_payload = [
            RunLogResponse.model_validate(log_row).model_dump(mode="json")
            for log_row in logs
        ]
        await websocket.send_json({"type": "history", "logs": logs_payload})

        # 4. If run is already terminal, send 'done' and finish
        if run.status in TERMINAL_STATUSES:
            await websocket.send_json(
                {"type": "done", "run_id": str(run.id), "status": run.status}
            )
            await websocket.close()
            return

    finally:
        db.close()

    # 5. For active runs, subscribe to live queue
    q = run_worker.subscribe(str(run_id))
    try:
        while True:
            try:
                # Wait for up to 0.5s for new messages
                msg = await asyncio.to_thread(q.get, timeout=0.5)
                await websocket.send_json(msg)
                if msg.get("type") == "done":
                    break
            except queue.Empty:
                # Periodic check if client disconnected or run became terminal
                with SessionLocal() as check_db:
                    check_run = check_db.get(Run, run_id)
                    if check_run and check_run.status in TERMINAL_STATUSES:
                        await websocket.send_json(
                            {"type": "done", "run_id": str(run_id), "status": check_run.status}
                        )
                        break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        pass
    finally:
        run_worker.unsubscribe(str(run_id), q)
        with contextlib.suppress(Exception):
            await websocket.close()
