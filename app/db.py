"""SQLAlchemy-Engine, Session-Factory und Schema-Setup.

DB-agnostisch: läuft mit SQLite (Default, out-of-the-box) genauso wie mit Postgres
(``DATABASE_URL=postgresql+psycopg://…``). Alle gespeicherten Zeitstempel sind
naive UTC, damit SQLite und Postgres identisch vergleichen (SQLite kennt keine tz).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC — konsistent über SQLite und Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_engine():
    url = settings.DATABASE_URL
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        # BackgroundTasks laufen in einem anderen Thread → check_same_thread aus.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, class_=Session
)


def init_db() -> None:
    from app import models  # noqa: F401 — Modelle an Base registrieren

    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI-Dependency: committet bei Erfolg, rollt bei Fehler zurück."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
