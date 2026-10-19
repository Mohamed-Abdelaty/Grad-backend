import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.api.routes import websocket


def test_websocket_manager_connect_disconnect():
    mgr = websocket.ConnectionManager()
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(mgr.connect(ws))
        assert ws in mgr.active_connections
        mgr.disconnect(ws)
        assert ws not in mgr.active_connections
    finally:
        loop.close()


def test_websocket_broadcast_and_send():
    mgr = websocket.ConnectionManager()
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    ws1.send_json = AsyncMock()
    ws2.send_json = AsyncMock()
    mgr.active_connections = [ws1, ws2]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(mgr.broadcast({"msg": "hi"}))
        assert ws1.send_json.called
        assert ws2.send_json.called
    finally:
        loop.close()


def test_websocket_send_personal_and_remove_on_error():
    mgr = websocket.ConnectionManager()
    ws_bad = AsyncMock()
    ws_bad.send_json = AsyncMock(side_effect=Exception("boom"))
    mgr.active_connections = [ws_bad]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(mgr.send_personal_message({"x": 1}, ws_bad))
        assert ws_bad not in mgr.active_connections
    finally:
        loop.close()
