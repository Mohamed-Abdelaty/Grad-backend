"""Local Webhook Listener for ChaosFlow notification testing.

Listens on http://127.0.0.1:9000/webhook (or configurable port), prints incoming
headers and JSON body, and validates the HMAC-SHA256 signature using the secret.

Usage:
    python scripts/webhook_listener.py [--port 9000] [--secret top-secret-webhook-key-123456]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_SECRET = "top-secret-webhook-key-123456"
DEFAULT_PORT = 9000


class WebhookHandler(BaseHTTPRequestHandler):
    secret: str = DEFAULT_SECRET

    def do_GET(self) -> None:
        """Friendly health/test endpoint so opening in a browser returns 200 OK."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {
            "status": "listening",
            "message": "ChaosFlow Webhook Listener is active and waiting for POST events on /webhook",
            "endpoint": "/webhook",
            "method_expected": "POST",
        }
        self.wfile.write(json.dumps(response, indent=2).encode("utf-8"))

    def do_POST(self) -> None:
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "Not Found"}')
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length)

        # Headers
        signature = self.headers.get("X-Signature-256", "")
        timestamp = self.headers.get("X-Timestamp", "")

        print("\n" + "=" * 60)
        print(f"[!] RECEIVED WEBHOOK EVENT ({content_length} bytes)")
        print("=" * 60)
        print(f"X-Signature-256: {signature}")
        print(f"X-Timestamp:     {timestamp}")

        # Compute HMAC
        # Expected signature message format: f"{timestamp}.{body_str}"
        body_str = body_bytes.decode("utf-8", errors="replace")
        expected_msg = f"{timestamp}.{body_str}"
        computed_sig = hmac.new(
            self.secret.encode("utf-8"),
            expected_msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        is_valid = hmac.compare_digest(signature, computed_sig) if signature else False

        if is_valid:
            print("[STATUS] SIGNATURE VERIFIED: SUCCESS (Authentic event from ChaosFlow)")
        else:
            print("[STATUS] SIGNATURE VERIFIED: FAILED (Signature mismatch or missing)")
            print(f"         Expected: {computed_sig}")
            print(f"         Received: {signature}")

        print("-" * 60)
        print("BODY JSON:")
        try:
            parsed = json.loads(body_str)
            print(json.dumps(parsed, indent=2))
        except Exception:
            print(body_str)
        print("=" * 60 + "\n")

        # Reply 200 OK
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "received", "signature_valid": ' + (b"true" if is_valid else b"false") + b'}')

    def log_message(self, format: str, *args: object) -> None:
        """Suppress standard http.server logging to keep terminal output clean."""
        return


def run_listener(port: int = DEFAULT_PORT, secret: str = DEFAULT_SECRET) -> None:
    WebhookHandler.secret = secret
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, WebhookHandler)
    print("=" * 60)
    print(f" ChaosFlow Webhook Listener running on http://127.0.0.1:{port}/webhook")
    print(f" Secret configured: {secret}")
    print(" Waiting for events... (Press Ctrl+C to stop)")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping webhook listener.")
        httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ChaosFlow Webhook Listener")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on (default 9000)")
    parser.add_argument("--secret", type=str, default=DEFAULT_SECRET, help="Signing secret to verify HMAC")
    args = parser.parse_args()
    run_listener(port=args.port, secret=args.secret)
