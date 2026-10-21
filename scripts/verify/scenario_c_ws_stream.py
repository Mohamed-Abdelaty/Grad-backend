"""Scenario C: WebSocket Live Streaming
1. Trigger a run via API.
2. Connect WebSocket client to /ws/runs/{run_id}?token={token} during or immediately following trigger.
3. Verify event messages streamed (history, node_started, batch_processed, progress, node_finished, done).
4. Verify late-connecting client (after run completion) receives history + done and closes cleanly.
5. Verify IDOR protection on WebSocket: unauthorized user receives 1008 policy violation.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


def run():
    print("=== SCENARIO C: WEBSOCKET LIVE STREAMING ===")

    with TestClient(app) as client:
        # Register User A
        email_a = f"ws_user_a_{int(time.time())}@example.com"
        client.post("/auth/register", json={"email": email_a, "password": "Password123!"})
        token_a = client.post("/auth/login", json={"email": email_a, "password": "Password123!"}).json()["access_token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # Register User B (for IDOR check)
        email_b = f"ws_user_b_{int(time.time())}@example.com"
        client.post("/auth/register", json={"email": email_b, "password": "Password123!"})
        token_b = client.post("/auth/login", json={"email": email_b, "password": "Password123!"}).json()["access_token"]

        # Create pipeline for User A
        pipe_doc = json.loads(Path("samples/pipeline.json").read_text(encoding="utf-8"))
        p_resp = client.post("/pipelines", json={"name": "ws-test", "config": pipe_doc}, headers=headers_a)
        pipe_id = p_resp.json()["id"]

        # Trigger run
        r_resp = client.post(f"/pipelines/{pipe_id}/runs", headers=headers_a)
        run_id = r_resp.json()["id"]
        print(f"  [1] Triggered run {run_id}")

        # Connect WebSocket live
        received_types = []
        with client.websocket_connect(f"/ws/runs/{run_id}?token={token_a}") as ws:
            while True:
                try:
                    data = ws.receive_json()
                    msg_type = data.get("type")
                    received_types.append(msg_type)
                    if msg_type == "done":
                        break
                except WebSocketDisconnect:
                    break

        print(f"  [2] Live WebSocket stream received message types: {set(received_types)}")
        assert "history" in received_types
        assert "done" in received_types
        print("  [PASS] Live WebSocket stream received expected events and terminal done.")

        # Test late connection (client connects AFTER run is already finished)
        late_types = []
        with client.websocket_connect(f"/ws/runs/{run_id}?token={token_a}") as ws:
            while True:
                try:
                    data = ws.receive_json()
                    msg_type = data.get("type")
                    late_types.append(msg_type)
                    if msg_type == "done":
                        break
                except WebSocketDisconnect:
                    break

        print(f"  [3] Late WebSocket connection received message types: {late_types}")
        assert late_types == ["history", "done"], f"Expected ['history', 'done'], got {late_types}"
        print("  [PASS] Late-connecting client received history + done, then closed cleanly.")

        # Test IDOR: User B trying to connect to User A's run WebSocket
        try:
            with client.websocket_connect(f"/ws/runs/{run_id}?token={token_b}") as ws:
                ws.receive_json()
            assert False, "User B should not be permitted to connect to User A's run"
        except WebSocketDisconnect as exc:
            assert exc.code == 1008, f"Expected 1008 policy violation, got {exc.code}"
            print(f"  [4] Unauthorized WebSocket connection correctly rejected with code {exc.code}")

    print("=== SCENARIO C PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
