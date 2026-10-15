"""FastAPI application factory and router wiring.

Route contract: canonical paths are exactly those in
``ChaosFlow_Backend_Tasks.pdf`` (``/auth/*``, ``/pipelines*``, ``/runs*``,
``/plugins``, ``/health``, ``WS /ws/runs/{id}``). Every router is *also*
mounted under the ``/api`` prefix because the overall project guide uses
``/api/*`` paths; both spellings serve the same handlers, so the published
contract is explicit and frontend-compatible either way.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, pipelines, runs, system, websocket
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging_config import configure_logging, new_request_id
from app.db.session import Base
from app.db.session import engine as db_engine

# Import models so metadata is complete for create_all / Alembic autogenerate.
from app.models import entities as _entities  # noqa: F401

logger = logging.getLogger("chaosflow")


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Startup: ensure tables exist (dev) and seed admin; shutdown: nothing."""
    _ = app
    # Dev convenience: ensure tables exist. Production uses Alembic
    # (`alembic upgrade head`); create_all is idempotent and safe.
    Base.metadata.create_all(bind=db_engine)
    _seed_admin()
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    configure_logging(settings.LOG_LEVEL)
    app = FastAPI(
        title="ChaosFlow API",
        version=settings.APP_VERSION,
        description="Configurable, resilient data-pipeline platform: engine + REST API.",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID", new_request_id())
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "%s %s -> %s (%.3fs)",
            request.method,
            request.url.path,
            getattr(response, "status_code", "?"),
            time.monotonic() - started,
            extra={"request_id": request_id},
        )
        return response

    for route_module in (auth.router, pipelines.router, runs.router, system.router):
        app.include_router(route_module)
        app.include_router(route_module, prefix="/api")
    app.include_router(websocket.router)
    app.include_router(websocket.router, prefix="/api")

    return app


def _seed_admin() -> None:
    """Create the initial admin from ADMIN_EMAIL/ADMIN_PASSWORD when set."""
    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        return
    from app.core.security import hash_password
    from app.db.session import SessionLocal
    from app.models.entities import User

    db = SessionLocal()
    try:
        email = settings.ADMIN_EMAIL.strip().lower()
        if db.query(User).filter(User.email == email).first() is None:
            db.add(
                User(
                    email=email, password_hash=hash_password(settings.ADMIN_PASSWORD), role="admin"
                )
            )
            db.commit()
            logger.info("Seeded admin user %s", email)
    except Exception as exc:
        logger.error("Admin seeding failed: %s", exc)
    finally:
        db.close()


app = create_app()
