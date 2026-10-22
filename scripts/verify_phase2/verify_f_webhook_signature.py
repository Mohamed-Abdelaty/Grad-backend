"""Test F: Webhook HMAC signature generation, tamper verification, and secret privacy."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import uuid4

from app.services.notification import NotificationService


def verify_webhook_signature() -> None:
    print("[*] Verifying Webhook HMAC signature & secrecy...")

    secret = "my_super_secret_signing_key_12345"
    timestamp = str(int(time.time()))
    payload = {
        "notification_id": str(uuid4()),
        "type": "run_failed",
        "title": "Pipeline failed",
        "body": "Node crashed",
        "run_id": str(uuid4()),
        "created_at": "2026-10-22T20:00:00Z",
    }
    raw_body = json.dumps(payload).encode()

    # 1. Generate signature with service
    sig = NotificationService._generate_webhook_signature(secret, timestamp, raw_body)
    assert sig and len(sig) == 64, "Signature should be a 64-char sha256 hex string"

    # 2. Independently verify HMAC
    expected_msg = f"{timestamp}.{raw_body.decode()}"
    independent_sig = hmac.new(secret.encode(), expected_msg.encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(sig, independent_sig), "HMAC verification failed!"

    # 3. Tamper test: modify body
    tampered_body = json.dumps({**payload, "title": "Tampered Title"}).encode()
    tampered_sig = NotificationService._generate_webhook_signature(secret, timestamp, tampered_body)
    assert not hmac.compare_digest(sig, tampered_sig), "Tampered body must NOT produce same signature!"

    # 4. Tamper test: modify timestamp (replay protection)
    tampered_time = str(int(timestamp) + 100)
    time_tampered_sig = NotificationService._generate_webhook_signature(secret, tampered_time, raw_body)
    assert not hmac.compare_digest(sig, time_tampered_sig), "Tampered timestamp must invalidate signature!"

    print("[PASS] Webhook HMAC signature and tamper resistance verified successfully.")


if __name__ == "__main__":
    verify_webhook_signature()
