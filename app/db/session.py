"""Database engine, sessions, and declarative base."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _make_engine():  # type: ignore[no-untyped-def]
    url = settings.DATABASE_URL
    kwargs: dict[str, object] = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)  # type: ignore[arg-type]


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():  # type: ignore[no-untyped-def]
    """FastAPI dependency yielding a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
