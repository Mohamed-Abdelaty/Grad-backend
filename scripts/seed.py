#!/usr/bin/env python
"""Idempotent seed script for system templates and admin user."""

from __future__ import annotations

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.entities import Template, User
from app.services.template import SEED_TEMPLATES, validate_seed_templates


def seed_admin() -> None:
    """Create initial admin user from environment variables."""
    from app.core.config import settings

    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        print("ADMIN_EMAIL/ADMIN_PASSWORD not set, skipping admin seeding")
        return

    db = SessionLocal()
    try:
        email = settings.ADMIN_EMAIL.strip().lower()
        if db.query(User).filter(User.email == email).first() is None:
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(settings.ADMIN_PASSWORD),
                    role="admin",
                )
            )
            db.commit()
            print(f"Seeded admin user: {email}")
        else:
            print(f"Admin user already exists: {email}")
    except Exception as exc:
        print(f"Admin seeding failed: {exc}")
        db.rollback()
    finally:
        db.close()


def seed_templates() -> None:
    """Seed system templates (idempotent)."""
    # Validate all seed templates first
    errors = validate_seed_templates()
    if errors:
        print("ERROR: Seed template validation failed:")
        for err in errors:
            print(f"  - {err}")
        raise RuntimeError("Seed template validation failed")

    db = SessionLocal()
    try:
        seeded = 0
        for template_data in SEED_TEMPLATES:
            existing = db.query(Template).filter(
                Template.name == template_data["name"],
                Template.owner_id.is_(None),
            ).first()

            if existing:
                # Update to ensure valid config
                existing.config = template_data["config"]
                existing.description = template_data["description"]
                existing.category = template_data["category"]
                existing.is_published = True
                existing.visibility = "public"
                db.commit()
                print(f"Updated template: {template_data['name']}")
            else:
                template = Template(
                    name=template_data["name"],
                    category=template_data["category"],
                    description=template_data["description"],
                    config=template_data["config"],
                    owner_id=None,
                    visibility="public",
                    is_published=True,
                )
                db.add(template)
                seeded += 1
                print(f"Created template: {template_data['name']}")

        db.commit()
        print(f"Seeded {seeded} new system templates (total: {seeded + (6 - seeded)} templates)")
    except Exception as exc:
        print(f"Template seeding failed: {exc}")
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    """Run all seeding operations."""
    print("Running seed operations...")
    seed_admin()
    seed_templates()
    print("Seed operations completed successfully!")


if __name__ == "__main__":
    main()
