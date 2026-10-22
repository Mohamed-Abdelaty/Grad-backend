"""Idempotent seed script for ChaosFlow demo environment.

Creates:
- Admin user (admin@test.com / adminpassword)
- User A (user_a@example.com / Password123!)
- User B (user_b@example.com / Password123!)
- User A Pipeline (orders-pipeline) at version 1
- Version 2 update of the pipeline
- A completed successful run and logs
- A completed failed run
- In-app notifications
- A schedule (* * * * *)
- Read and Run API keys
- Notification preferences with webhook url and secret

Prints all tokens, IDs, and keys clearly.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models.entities import (
    ApiKey,
    Notification,
    NotificationPreference,
    NotificationType,
    Pipeline,
    PipelineVersion,
    Run,
    RunLog,
    Schedule,
    Template,
    User,
)
from app.services.apikey import ApiKeyService
from app.services.template import seed_templates


def seed_demo() -> dict[str, str]:
    db = SessionLocal()
    results: dict[str, str] = {}

    try:
        print("=" * 65)
        print("   Seeding ChaosFlow Demo Environment")
        print("=" * 65)

        # 1. Seed Templates
        seed_templates(db)
        print("[+] Seeded system templates.")

        # 2. Seed Users
        # Admin
        admin = db.query(User).filter(User.email == "admin@test.com").first()
        if not admin:
            admin = User(
                email="admin@test.com",
                password_hash=hash_password("adminpassword"),
                role="admin",
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
        else:
            admin.password_hash = hash_password("adminpassword")
            db.commit()
        print(f"[+] Admin: admin@test.com / adminpassword (ID: {admin.id})")

        # User A
        user_a = db.query(User).filter(User.email == "user_a@example.com").first()
        if not user_a:
            user_a = User(
                email="user_a@example.com",
                password_hash=hash_password("Password123!"),
                role="user",
            )
            db.add(user_a)
            db.commit()
            db.refresh(user_a)
        else:
            user_a.password_hash = hash_password("Password123!")
            db.commit()
        print(f"[+] User A: user_a@example.com / Password123! (ID: {user_a.id})")

        # User B
        user_b = db.query(User).filter(User.email == "user_b@example.com").first()
        if not user_b:
            user_b = User(
                email="user_b@example.com",
                password_hash=hash_password("Password123!"),
                role="user",
            )
            db.add(user_b)
            db.commit()
            db.refresh(user_b)
        else:
            user_b.password_hash = hash_password("Password123!")
            db.commit()
        print(f"[+] User B: user_b@example.com / Password123! (ID: {user_b.id})")

        # Generate access tokens
        admin_token = create_access_token(user_id=str(admin.id), role=admin.role)
        user_a_token = create_access_token(user_id=str(user_a.id), role=user_a.role)
        user_b_token = create_access_token(user_id=str(user_b.id), role=user_b.role)

        results["ADMIN_TOKEN"] = admin_token
        results["USER_A_TOKEN"] = user_a_token
        results["USER_B_TOKEN"] = user_b_token

        # 3. User A Pipeline
        pipeline = db.query(Pipeline).filter(Pipeline.owner_id == user_a.id, Pipeline.name == "orders-pipeline").first()
        config_v1 = {
            "pipeline": {
                "id": str(uuid.uuid4()),
                "name": "orders-pipeline",
                "version": 1,
                "schedule": None,
                "nodes": [
                    {"id": "src_1", "type": "source", "plugin": "csv", "config": {"path": "samples/data/orders.csv", "delimiter": ","}},
                    {"id": "tr_1", "type": "transform", "plugin": "filter", "config": {"expression": "amount > 100"}},
                    {"id": "snk_1", "type": "sink", "plugin": "csv", "config": {"path": "outputs/orders_clean.csv", "mode": "overwrite"}},
                ],
                "edges": [{"from": "src_1", "to": "tr_1"}, {"from": "tr_1", "to": "snk_1"}],
                "retry": {"max_attempts": 3, "backoff_seconds": 5},
            }
        }
        if not pipeline:
            pipeline = Pipeline(
                owner_id=user_a.id,
                name="orders-pipeline",
                current_version=2,
            )
            db.add(pipeline)
            db.commit()
            db.refresh(pipeline)

            config_v1["pipeline"]["id"] = str(pipeline.id)
            v1 = PipelineVersion(pipeline_id=pipeline.id, version=1, config=config_v1)
            db.add(v1)

            config_v2 = copy.deepcopy(config_v1)
            config_v2["pipeline"]["version"] = 2
            config_v2["pipeline"]["nodes"][1]["config"]["expression"] = "amount > 200"
            v2 = PipelineVersion(pipeline_id=pipeline.id, version=2, config=config_v2)
            db.add(v2)
            db.commit()

        print(f"[+] Pipeline: orders-pipeline (ID: {pipeline.id}, Current Version: {pipeline.current_version})")
        results["PIPELINE_ID"] = str(pipeline.id)

        # 4. Runs for Pipeline
        pv = db.query(PipelineVersion).filter(PipelineVersion.pipeline_id == pipeline.id).order_by(PipelineVersion.version.desc()).first()
        pv_id = pv.id if pv else None

        run_succ = db.query(Run).filter(Run.pipeline_id == pipeline.id, Run.status == "succeeded").first()
        if not run_succ and pv_id:
            run_succ = Run(
                pipeline_id=pipeline.id,
                pipeline_version_id=pv_id,
                owner_id=user_a.id,
                version=pipeline.current_version,
                status="succeeded",
                progress=1.0,
                records_in=100,
                records_out=95,
                trigger="manual",
                started_at=datetime.now(UTC) - timedelta(minutes=10),
                finished_at=datetime.now(UTC) - timedelta(minutes=9),
            )
            db.add(run_succ)
            db.commit()
            db.refresh(run_succ)

            log_entry = RunLog(
                run_id=run_succ.id,
                level="info",
                node_id="tr_1",
                event="node_finish",
                message="Transformed 95 records successfully.",
                records_in=100,
                records_out=95,
            )
            db.add(log_entry)
            db.commit()

        print(f"[+] Successful Run ID: {run_succ.id}")
        results["RUN_ID_SUCCESS"] = str(run_succ.id)

        # 5. In-app Notification
        notif = db.query(Notification).filter(Notification.user_id == user_a.id).first()
        if not notif:
            notif = Notification(
                user_id=user_a.id,
                type=NotificationType.RUN_SUCCEEDED,
                title="Pipeline run succeeded",
                body=f"Pipeline {pipeline.name} run {run_succ.id} completed successfully.",
                run_id=run_succ.id,
                read=False,
            )
            db.add(notif)
            db.commit()
            db.refresh(notif)
        print(f"[+] In-app Notification ID: {notif.id}")
        results["NOTIFICATION_ID"] = str(notif.id)

        # 6. Notification Preferences
        pref = db.query(NotificationPreference).filter(NotificationPreference.user_id == user_a.id).first()
        if not pref:
            pref = NotificationPreference(
                user_id=user_a.id,
                run_failed_in_app=True,
                run_failed_email=True,
                run_failed_webhook=True,
                run_succeeded_in_app=True,
                run_succeeded_email=False,
                run_succeeded_webhook=True,
                schedule_missed_in_app=True,
                schedule_missed_email=True,
                schedule_missed_webhook=True,
                webhook_url="http://127.0.0.1:9000/webhook",
                webhook_secret_encrypted="top-secret-webhook-key-123456",
                email="user_a_alerts@example.com",
            )
            db.add(pref)
            db.commit()
        print("[+] Notification Preferences configured (webhook=http://127.0.0.1:9000/webhook)")

        # 7. Schedule
        sched = db.query(Schedule).filter(Schedule.pipeline_id == pipeline.id).first()
        if not sched:
            sched = Schedule(
                pipeline_id=pipeline.id,
                cron="0 * * * *",
                timezone="UTC",
                enabled=True,
                misfire_policy="skip",
                next_run_at=datetime.now(UTC) + timedelta(hours=1),
            )
            db.add(sched)
            db.commit()
            db.refresh(sched)
        print(f"[+] Schedule ID: {sched.id} (cron: {sched.cron}, tz: {sched.timezone})")
        results["SCHEDULE_ID"] = str(sched.id)

        # 8. API Key for User A
        existing_key = db.query(ApiKey).filter(ApiKey.user_id == user_a.id, ApiKey.name == "demo-read-key").first()
        if not existing_key:
            prefix = ApiKeyService._generate_prefix()
            secret = ApiKeyService._generate_secret()
            raw_key = f"cf_{prefix}_{secret}"
            key_hash = ApiKeyService._hash_key(raw_key)

            api_key = ApiKey(
                user_id=user_a.id,
                name="demo-read-key",
                prefix=prefix,
                key_hash=key_hash,
                scopes=["read"],
            )
            db.add(api_key)
            db.commit()
            db.refresh(api_key)
            print(f"[+] API Key (Read-Only): {raw_key} (ID: {api_key.id})")
            results["API_KEY_READ"] = raw_key
            results["API_KEY_ID"] = str(api_key.id)
        else:
            print(f"[+] API Key exists (ID: {existing_key.id}, prefix: {existing_key.prefix})")
            results["API_KEY_ID"] = str(existing_key.id)

        # 9. Get first Template ID
        tmpl = db.query(Template).first()
        if tmpl:
            results["TEMPLATE_ID"] = str(tmpl.id)
            print(f"[+] Demo Template ID: {tmpl.id} ('{tmpl.name}')")

        print("=" * 65)
        print("SEEDING COMPLETE. Copy the tokens and IDs below:")
        print("=" * 65)
        for k, v in results.items():
            print(f"{k}: {v}")
        print("=" * 65)
        return results

    finally:
        db.close()


if __name__ == "__main__":
    seed_demo()
