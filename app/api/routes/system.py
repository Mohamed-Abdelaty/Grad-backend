"""Plugin catalog and health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.config import settings
from app.db.session import engine as db_engine
from app.schemas.run import PluginEntry
from engine.registry import catalog

router = APIRouter(tags=["system"])


@router.get("/plugins", response_model=list[PluginEntry])
def list_plugins(category: str | None = Query(default=None)) -> list[PluginEntry]:
    """List registered plugins with their config JSON Schemas (from the live registry)."""
    entries = catalog()
    if category is not None:
        entries = [e for e in entries if e.get("category") == category]
    return [
        PluginEntry(category=e["category"], name=e["name"], config_schema=e["config_schema"])
        for e in entries
    ]  # type: ignore[typeddict-item]


@router.get("/health")
def health() -> dict[str, object]:
    """Liveness + database reachability (used by CI, Compose, and load balancers)."""
    db_status = "up"
    try:
        with db_engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception:
        db_status = "down"
    return {
        "status": "ok" if db_status == "up" else "degraded",
        "version": settings.APP_VERSION,
        "database": db_status,
    }
