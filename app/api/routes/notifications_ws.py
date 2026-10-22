"""WebSocket endpoint for real-time notification streaming."""

from __future__ import annotations

import asyncio
import contextlib
import queue
import uuid
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.security import TokenError, decode_token
from app.db.session import SessionLocal
from app.models.entities import Notification, User
from app.schemas.notification import NotificationResponse
from app.services.notification import NotificationService

router = APIRouter()

# Global notification queue per user
_notification_queues: dict[str, list[queue.Queue]] = {}
_queue_lock = asyncio.Lock()


async def notify_user(user_id: UUID, notification: Notification) -> None:
    """Send a notification to all active WebSocket connections for a user."""
    user_id_str = str(user_id)
    async with _queue_lock:
        queues = _notification_queues.get(user_id_str, [])

    for q in queues:
        with contextlib.suppress(Exception):
            q.put_nowait(notification)


def _notify_user_sync(user_id: UUID, notification: Notification) -> None:
    """Synchronous wrapper for notifying user (for use in non-async contexts)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(lambda: asyncio.create_task(notify_user(user_id, notification)))
        else:
            loop.run_until_complete(notify_user(user_id, notification))
    except Exception:
        pass  # Best effort


@router.websocket("/ws/notifications")
async def notifications_websocket(
    websocket: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    """Real-time notification streaming for authenticated user."""
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

    # 2. Check user exists and is active
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()

        # 3. Send unread count on connect
        unread_count = NotificationService.get_unread_count(db, user_id)
        await websocket.send_json({"type": "unread_count", "count": unread_count})

        # 4. Register queue for this connection
        q: queue.Queue = queue.Queue()
        async with _queue_lock:
            _notification_queues.setdefault(str(user_id), []).append(q)

    finally:
        db.close()

    # 5. Listen for notifications
    try:
        while True:
            try:
                # Wait for notification with timeout
                notification = await asyncio.to_thread(q.get, timeout=1.0)
                await websocket.send_json(
                    NotificationResponse.model_validate(notification).model_dump(mode="json")
                )
            except queue.Empty:
                # Periodic heartbeat / connection check
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        pass
    finally:
        # Cleanup
        async with _queue_lock:
            queues = _notification_queues.get(str(user_id), [])
            if q in queues:
                queues.remove(q)
            if not queues:
                _notification_queues.pop(str(user_id), None)
        with contextlib.suppress(Exception):
            await websocket.close()
