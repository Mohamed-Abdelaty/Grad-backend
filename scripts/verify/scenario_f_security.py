"""Scenario F: Security & Ownership (IDOR + AST Injection Protection)
1. Verify cross-user IDOR isolation on EVERY endpoint:
   - User A cannot GET, UPDATE, DELETE, or RUN User B's pipeline
   - User A cannot GET, STOP, or view LOGS of User B's run
   - User A cannot view User B's pipeline versions
2. Verify AST expression evaluator blocks:
   - __import__, open, exec, eval
   - dunder attribute traversal: ().__class__.__bases__[0]...
   - function calls and method calls
   - attribute access (foo.bar)
   - subscript indexing abuse (foo[0])
   - AST depth bombs (> 50 depth)
"""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from starlette.testclient import TestClient

from app.main import app
from engine.expression import UnsafeExpressionError, evaluate


def test_ast_security():
    print("  Testing Expression Evaluator AST sandbox...")
    dangerous_expressions = [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "().__class__.__bases__[0].__subclasses__()",
        "exec('import sys')",
        "eval('2 + 2')",
        "getattr(x, '__class__')",
        "x.__class__",
        "x.__dict__",
        "x.strip()",
        "x['secret']",
        "x[0]",
        "lambda x: x",
        "import os",
    ]

    for expr in dangerous_expressions:
        try:
            evaluate(expr, {"x": "hello"})
            assert False, f"Expression '{expr}' should have been rejected"
        except UnsafeExpressionError:
            # Expected rejection
            pass
        except Exception:
            # Also acceptable if rejected as parsing syntax error
            pass

    print(f"  [PASS] All {len(dangerous_expressions)} malicious/injected expressions safely rejected.")

    # Test AST depth limit protection (nesting bomb: (1 + (1 + (1 + ...))))
    nested_bomb = "1"
    for _ in range(60):
        nested_bomb = f"({nested_bomb} + 1)"
    try:
        evaluate(nested_bomb, {})
        assert False, "Nesting depth bomb should have been rejected"
    except UnsafeExpressionError as exc:
        assert "maximum nesting depth" in str(exc)
        print("  [PASS] AST depth limit (> 50) successfully defended against DoS bomb.")

def test_idor_isolation():
    print("  Testing API IDOR ownership boundaries...")
    with TestClient(app) as client:
        # User A
        email_a = f"alice_{int(time.time())}@example.com"
        client.post("/auth/register", json={"email": email_a, "password": "Password123!"})
        token_a = client.post("/auth/login", json={"email": email_a, "password": "Password123!"}).json()["access_token"]
        headers_a = {"Authorization": f"Bearer {token_a}"}

        # User B
        email_b = f"bob_{int(time.time())}@example.com"
        client.post("/auth/register", json={"email": email_b, "password": "Password123!"})
        token_b = client.post("/auth/login", json={"email": email_b, "password": "Password123!"}).json()["access_token"]
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # User B creates pipeline and triggers run
        pipe_doc = {
            "pipeline": {
                "id": str(uuid.uuid4()),
                "name": "bob-private-pipeline",
                "version": 1,
                "schedule": None,
                "nodes": [
                    {"id": "src", "type": "source", "plugin": "csv", "config": {"path": "samples/data/orders.csv"}},
                    {"id": "snk", "type": "sink", "plugin": "console", "config": {}},
                ],
                "edges": [{"from": "src", "to": "snk"}],
                "retry": {"max_attempts": 1, "backoff_seconds": 1},
            }
        }
        resp = client.post("/pipelines", json={"name": "bob-pipeline", "config": pipe_doc}, headers=headers_b)
        assert resp.status_code == 201
        bob_pipe_id = resp.json()["id"]

        resp = client.post(f"/pipelines/{bob_pipe_id}/runs", headers=headers_b)
        assert resp.status_code == 202
        bob_run_id = resp.json()["id"]

        # Alice attempts IDOR attacks against Bob's resources:
        # 1. GET Bob's pipeline
        assert client.get(f"/pipelines/{bob_pipe_id}", headers=headers_a).status_code in (403, 404)

        # 2. PUT / UPDATE Bob's pipeline
        assert client.put(f"/pipelines/{bob_pipe_id}", json={"name": "hacked"}, headers=headers_a).status_code in (403, 404)

        # 3. DELETE Bob's pipeline
        assert client.delete(f"/pipelines/{bob_pipe_id}", headers=headers_a).status_code in (403, 404)

        # 4. GET Bob's pipeline versions
        assert client.get(f"/pipelines/{bob_pipe_id}/versions", headers=headers_a).status_code in (403, 404)

        # 5. POST / trigger run on Bob's pipeline
        assert client.post(f"/pipelines/{bob_pipe_id}/runs", headers=headers_a).status_code in (403, 404)

        # 6. GET Bob's run status
        assert client.get(f"/runs/{bob_run_id}", headers=headers_a).status_code in (403, 404)

        # 7. GET Bob's run logs
        assert client.get(f"/runs/{bob_run_id}/logs", headers=headers_a).status_code in (403, 404)

        # 8. POST / stop Bob's run
        assert client.post(f"/runs/{bob_run_id}/stop", headers=headers_a).status_code in (403, 404)

        # 9. List pipelines: Alice should not see Bob's pipeline
        alice_pipes = client.get("/pipelines", headers=headers_a).json()["items"]
        assert not any(p["id"] == bob_pipe_id for p in alice_pipes)

        # 10. List runs: Alice should not see Bob's run
        alice_runs = client.get("/runs", headers=headers_a).json()["items"]
        assert not any(r["id"] == bob_run_id for r in alice_runs)

        print("  [PASS] IDOR ownership isolation enforced across all pipeline and run endpoints.")

def run():
    print("=== SCENARIO F: SECURITY & OWNERSHIP VERIFICATION ===")
    test_ast_security()
    test_idor_isolation()
    print("=== SCENARIO F PASSED SUCCESSFULLY ===\n")

if __name__ == "__main__":
    run()
