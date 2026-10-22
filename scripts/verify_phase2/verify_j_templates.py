"""Test J: Templates seeding, cloning, visibility, and atomic usage count."""

from __future__ import annotations

import concurrent.futures
import uuid

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.entities import Template
from app.services.template import SEED_TEMPLATES, seed_templates, validate_seed_templates


def verify_templates() -> None:
    print("[*] Verifying Templates seeding, visibility, and usage count...")
    client = TestClient(app)

    # 1. Validation check on all seed templates
    errors = validate_seed_templates()
    assert not errors, f"Seed templates failed validation: {errors}"

    # 2. Seed idempotency
    db = SessionLocal()
    try:
        count_first = seed_templates(db)
        count_second = seed_templates(db)
        assert count_second == 0, f"Seed is not idempotent! Added {count_second} duplicates on second run"
        total_seeded = db.query(Template).filter(Template.owner_id.is_(None)).count()
        assert total_seeded >= len(SEED_TEMPLATES)
    finally:
        db.close()

    # 3. User A & B visibility
    email_a = f"tpl_a_{uuid.uuid4().hex[:6]}@test.com"
    client.post("/auth/register", json={"email": email_a, "password": "Password123!"})
    token_a = client.post("/auth/login", json={"email": email_a, "password": "Password123!"}).json()["access_token"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    email_b = f"tpl_b_{uuid.uuid4().hex[:6]}@test.com"
    client.post("/auth/register", json={"email": email_b, "password": "Password123!"})
    token_b = client.post("/auth/login", json={"email": email_b, "password": "Password123!"}).json()["access_token"]
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # User A creates a private template
    tpl_create = client.post(
        "/templates",
        json={
            "name": "User A Private Template",
            "category": "ETL",
            "description": "Private template",
            "config": SEED_TEMPLATES[0]["config"],
        },
        headers=headers_a,
    )
    assert tpl_create.status_code == 201
    tpl_a_id = tpl_create.json()["id"]

    # User B cannot see it
    get_b = client.get(f"/templates/{tpl_a_id}", headers=headers_b)
    assert get_b.status_code == 404, f"User B should get 404 for User A's private template, got {get_b.status_code}"

    # User B cannot use it
    use_b = client.post(f"/templates/{tpl_a_id}/use", headers=headers_b)
    assert use_b.status_code == 404

    # Non-admin publish -> 403
    pub_res = client.post(f"/templates/{tpl_a_id}/publish", headers=headers_a)
    assert pub_res.status_code == 403, f"Non-admin publish should be 403, got {pub_res.status_code}"

    # 4. 20 parallel `use` calls -> usage_count exactly +20
    db = SessionLocal()
    try:
        tpl_before = db.get(Template, uuid.UUID(tpl_a_id))
        initial_count = tpl_before.usage_count
    finally:
        db.close()

    def do_use() -> int:
        c = TestClient(app)
        res = c.post(f"/templates/{tpl_a_id}/use", headers=headers_a)
        return res.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: do_use(), range(20)))

    assert all(code == 200 for code in results), f"Some use calls failed: {results}"

    db = SessionLocal()
    try:
        tpl_after = db.get(Template, uuid.UUID(tpl_a_id))
        final_count = tpl_after.usage_count
        assert final_count == initial_count + 20, f"Expected usage_count +20, got {final_count} (was {initial_count})"
    finally:
        db.close()

    print("[PASS] Templates verified successfully (seeds valid & idempotent, visibility isolated, parallel usage atomic).")


if __name__ == "__main__":
    verify_templates()
