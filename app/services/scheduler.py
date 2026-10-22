"""Scheduler service: APScheduler with SQLAlchemyJobStore, reconciliation, overlap prevention."""

from __future__ import annotations

import logging
import threading
import zoneinfo
from datetime import UTC, datetime, timedelta
from uuid import UUID

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.session import engine as db_engine
from app.models.entities import Pipeline, PipelineVersion, Run, Schedule
from app.services.audit import AuditAction, AuditService
from app.services.run_worker import submit_run

logger = logging.getLogger("chaosflow.scheduler")

# Advisory lock ID for ensuring only one scheduler instance fires jobs
SCHEDULER_LOCK_ID = settings.SCHEDULER_ADVISORY_LOCK_ID


class SchedulerService:
    """Manages scheduled pipeline runs using APScheduler."""

    _instance: SchedulerService | None = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.scheduler: BackgroundScheduler | None = None
        self._running = False

    def start(self) -> None:
        """Start the scheduler and reconcile jobs."""
        if self._running:
            return

        # Configure job store
        jobstores = {
            "default": SQLAlchemyJobStore(engine=db_engine)
        }

        self.scheduler = BackgroundScheduler(
            jobstores=jobstores,
            timezone=UTC,
            daemon=True,
        )
        self.scheduler.start()
        self._running = True

        # Reconcile schedules with jobs
        self.reconcile()

        logger.info("Scheduler started with %d jobs", len(self.scheduler.get_jobs()))

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        if self.scheduler and self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("Scheduler shut down")

    def reconcile(self) -> None:
        """Reconcile database schedules with APScheduler jobs.
        - Add missing jobs
        - Remove stale jobs
        - Update changed jobs
        """
        if not self.scheduler:
            return

        db = SessionLocal()
        try:
            # Get all enabled schedules from DB
            schedules = db.query(Schedule).filter(Schedule.enabled.is_(True)).all()
            db_schedule_ids = {str(s.id) for s in schedules}

            # Get existing APScheduler jobs
            aps_jobs = {job.id: job for job in self.scheduler.get_jobs()}

            # Remove stale jobs (in APScheduler but not in DB or disabled)
            for job_id, _job in aps_jobs.items():
                if job_id not in db_schedule_ids:
                    self.scheduler.remove_job(job_id)
                    logger.info("Removed stale scheduler job: %s", job_id)

            # Add or update jobs for each schedule
            for schedule in schedules:
                self._upsert_job(db, schedule)

            db.commit()
        except Exception as exc:
            logger.error("Scheduler reconciliation failed: %s", exc)
            db.rollback()
        finally:
            db.close()

    def _upsert_job(self, db: Session, schedule: Schedule) -> None:
        """Add or update a job for the given schedule."""
        if not self.scheduler:
            return

        job_id = str(schedule.id)

        # Validate cron expression
        try:
            croniter(schedule.cron, datetime.now(UTC))
        except Exception as exc:
            logger.error("Invalid cron expression for schedule %s: %s", job_id, exc)
            return

        # Validate timezone
        try:
            tz = zoneinfo.ZoneInfo(schedule.timezone)
        except Exception as exc:
            logger.error("Invalid timezone for schedule %s: %s", job_id, exc)
            return

        trigger = CronTrigger.from_crontab(schedule.cron, timezone=tz)

        # Check start/end dates
        start_date = schedule.start_at
        end_date = schedule.end_at

        existing_job = self.scheduler.get_job(job_id)
        if existing_job:
            # Update if changed
            if (existing_job.trigger != trigger or
                existing_job.next_run_time != self._compute_next_run(schedule, trigger)):
                self.scheduler.reschedule_job(job_id, trigger=trigger)
                logger.info("Updated scheduler job: %s", job_id)
        else:
            # Add new job
            self.scheduler.add_job(
                func=self._execute_scheduled_run,
                trigger=trigger,
                id=job_id,
                args=[str(schedule.pipeline_id)],
                start_date=start_date,
                end_date=end_date,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=settings.SCHEDULER_MISFIRE_GRACE_TIME,
            )
            logger.info("Added scheduler job: %s for pipeline %s", job_id, schedule.pipeline_id)

    def _compute_next_run(self, schedule: Schedule, trigger: CronTrigger) -> datetime | None:
        """Compute the next run time for a schedule."""
        try:
            return trigger.get_next_fire_time(datetime.now(UTC), None)
        except Exception:
            return None

    def _execute_scheduled_run(self, pipeline_id_str: str) -> None:
        """Execute a scheduled pipeline run with overlap prevention and missed run handling."""
        pipeline_id = UUID(pipeline_id_str)
        db = SessionLocal()
        try:
            # Acquire advisory lock to ensure only one scheduler instance fires
            if not self._acquire_lock(db):
                logger.debug("Could not acquire scheduler lock, skipping run for pipeline %s", pipeline_id)
                return

            schedule = db.query(Schedule).filter(Schedule.pipeline_id == pipeline_id).first()
            if not schedule or not schedule.enabled:
                return

            # Check for overlap: pending/running run for this pipeline
            overlap_run = db.query(Run).filter(
                Run.pipeline_id == pipeline_id,
                Run.status.in_(["queued", "running"]),
            ).first()

            now = datetime.now(UTC)

            if overlap_run:
                # Overlap detected - apply misfire policy
                logger.info("Overlap detected for pipeline %s, applying misfire policy: %s",
                           pipeline_id, schedule.misfire_policy)

                if schedule.misfire_policy == "skip":
                    # Record missed run and notify
                    self._record_missed_run(db, schedule, now, "overlap")
                elif schedule.misfire_policy == "run_once":
                    # Check if we already ran for this missed tick
                    if not self._already_ran_for_tick(db, schedule, now):
                        self._start_run(db, schedule, pipeline_id, now, trigger="scheduled")
                    else:
                        logger.info("Already ran for this tick, skipping")
                return

            # No overlap, start the run
            self._start_run(db, schedule, pipeline_id, now, trigger="scheduled")

            # Update next_run_at
            try:
                tz = zoneinfo.ZoneInfo(schedule.timezone)
                next_trigger = CronTrigger.from_crontab(schedule.cron, timezone=tz)
                schedule.next_run_at = self._compute_next_run(schedule, next_trigger)
            except Exception:
                pass
            db.commit()

        except Exception as exc:
            logger.error("Failed to execute scheduled run for pipeline %s: %s", pipeline_id, exc)
            db.rollback()
        finally:
            self._release_lock(db)
            db.close()

    def _acquire_lock(self, db: Session) -> bool:
        """Acquire PostgreSQL advisory lock for scheduler."""
        try:
            if settings.is_postgres:
                result = db.execute(text(f"SELECT pg_try_advisory_lock({SCHEDULER_LOCK_ID})"))
                return result.scalar() is True
            return True  # SQLite: no lock needed (single process)
        except Exception:
            return False

    def _release_lock(self, db: Session) -> None:
        """Release PostgreSQL advisory lock."""
        try:
            if settings.is_postgres:
                db.execute(text(f"SELECT pg_advisory_unlock({SCHEDULER_LOCK_ID})"))
        except Exception:
            pass

    def _already_ran_for_tick(self, db: Session, schedule: Schedule, now: datetime) -> bool:
        """Check if a scheduled run was already started for the current cron tick."""
        # Get the current cron tick start time
        try:
            tz = zoneinfo.ZoneInfo(schedule.timezone)
            cron = croniter(schedule.cron, now.astimezone(tz))
            tick_start = cron.get_prev(datetime).replace(tzinfo=tz).astimezone(UTC)
        except Exception:
            tick_start = now - timedelta(minutes=1)

        # Check if a scheduled run was started in this tick
        existing = db.query(Run).filter(
            Run.pipeline_id == schedule.pipeline_id,
            Run.trigger == "scheduled",
            Run.created_at >= tick_start,
            Run.created_at <= now,
        ).first()
        return existing is not None

    def _record_missed_run(self, db: Session, schedule: Schedule, now: datetime, reason: str) -> None:
        """Record a missed run and create schedule_missed notification."""
        schedule.last_run_status = "missed"
        db.commit()

        # Create notification for pipeline owner
        pipeline = db.get(Pipeline, schedule.pipeline_id)
        if pipeline:
            from app.services.notification import emit_schedule_missed

            try:
                emit_schedule_missed(db, pipeline.name, reason, pipeline.owner_id)
            except Exception as exc:
                logger.warning("Failed to emit missed run notification: %s", exc)

    def _start_run(self, db: Session, schedule: Schedule, pipeline_id: UUID, now: datetime, trigger: str) -> None:
        """Start a scheduled run using the same logic as manual runs."""
        pipeline = db.get(Pipeline, schedule.pipeline_id)
        if not pipeline:
            return

        version = db.query(PipelineVersion).filter(
            PipelineVersion.pipeline_id == pipeline.id,
            PipelineVersion.version == pipeline.current_version,
        ).first()
        if not version:
            logger.error("No current version for pipeline %s", pipeline_id)
            return

        run = Run(
            pipeline_id=pipeline.id,
            pipeline_version_id=version.id,
            owner_id=pipeline.owner_id,
            version=version.version,
            status="queued",
            progress=0.0,
            trigger=trigger,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        # Update schedule last_run_status
        schedule.last_run_status = "started"
        db.commit()

        # Submit to worker
        submit_run(str(run.id))

        # Audit
        AuditService.record(
            db,
            actor=pipeline.owner.email if pipeline.owner else "system",
            action=AuditAction.RUN_START,
            target_type="run",
            target_id=run.id,
            ip=None,
            metadata={"pipeline_id": str(pipeline.id), "trigger": trigger, "schedule_id": str(schedule.id)},
        )

        logger.info("Started scheduled run %s for pipeline %s", run.id, pipeline_id)


def get_scheduler() -> SchedulerService:
    """Get the singleton scheduler instance."""
    return SchedulerService()


def init_scheduler() -> SchedulerService:
    """Initialize and start the scheduler (call on app startup)."""
    scheduler = get_scheduler()
    if settings.SCHEDULER_ENABLED:
        scheduler.start()
    return scheduler


def shutdown_scheduler() -> None:
    """Shutdown the scheduler (call on app shutdown)."""
    scheduler = get_scheduler()
    scheduler.shutdown()


def preview_next_runs(cron: str, timezone: str, count: int = 5) -> list[datetime]:
    """Preview next N fire times for a cron expression in the given timezone."""
    fields = cron.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Cron expression must have exactly 5 fields (standard cron), got {len(fields)}")

    try:
        tz = zoneinfo.ZoneInfo(timezone)
    except Exception as exc:
        raise ValueError(f"Invalid timezone: {timezone}") from exc

    try:
        CronTrigger.from_crontab(cron, timezone=tz)
        base = datetime.now(tz)
        cron_iter = croniter(cron, base)
        return [cron_iter.get_next(datetime).astimezone(UTC) for _ in range(count)]
    except Exception as exc:
        raise ValueError(f"Invalid cron expression: {exc}") from exc


def handle_misfires_on_startup() -> None:
    """Handle misfires on startup for all schedules with misfire_policy=run_once."""
    db = SessionLocal()
    try:
        schedules = db.query(Schedule).filter(
            Schedule.enabled.is_(True),
            Schedule.misfire_policy == "run_once",
        ).all()

        now = datetime.now(UTC)
        for schedule in schedules:
            # Check if we missed any ticks since last run
            if schedule.next_run_at and schedule.next_run_at < now:
                # We missed a tick, start one run
                logger.info("Handling missed run for schedule %s", schedule.id)
                try:
                    tz = zoneinfo.ZoneInfo(schedule.timezone)
                    CronTrigger.from_crontab(schedule.cron, timezone=tz)
                except Exception:
                    continue

                # Check if already ran for this tick
                svc = SchedulerService()
                if not svc._already_ran_for_tick(db, schedule, now):
                    svc._start_run(db, schedule, schedule.pipeline_id, now, trigger="scheduled")

        db.commit()
    except Exception as exc:
        logger.error("Failed to handle misfires on startup: %s", exc)
        db.rollback()
    finally:
        db.close()
