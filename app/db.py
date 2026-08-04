"""SQLAlchemy-Engine, Session-Factory und Schema-Setup.

DB-agnostisch: läuft mit SQLite (Default, out-of-the-box) genauso wie mit Postgres
(``DATABASE_URL=postgresql+psycopg://…``). Alle gespeicherten Zeitstempel sind
naive UTC, damit SQLite und Postgres identisch vergleichen (SQLite kennt keine tz).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text
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


# Spalten, die nach dem ersten Release zur account-Tabelle kamen. create_all legt
# nur fehlende TABELLEN an, keine fehlenden Spalten → hier per ALTER nachziehen
# (SQLite und Postgres verstehen beide ADD COLUMN mit diesen generischen Typen).
_ACCOUNT_ADDED_COLUMNS: dict[str, str] = {
    "es_host": "VARCHAR(255)",
    "es_user": "VARCHAR(255)",
    "es_password_enc": "TEXT",
    "es_index": "VARCHAR(255)",
    "es_verify_certs": "BOOLEAN",
    "attachment_text": "BOOLEAN",
    "attachment_max_bytes": "BIGINT",
    "attachment_max_chars": "INTEGER",
}


def _ensure_account_columns() -> None:
    insp = inspect(engine)
    if "account" not in insp.get_table_names():
        return  # frische DB: create_all legt die Tabelle vollständig an
    existing = {c["name"] for c in insp.get_columns("account")}
    missing = {c: ddl for c, ddl in _ACCOUNT_ADDED_COLUMNS.items() if c not in existing}
    if not missing:
        return
    with engine.begin() as conn:
        for col, ddl in missing.items():
            conn.execute(text(f"ALTER TABLE account ADD COLUMN {col} {ddl}"))


def init_db() -> None:
    from app import models  # noqa: F401 — Modelle an Base registrieren

    Base.metadata.create_all(engine)
    _ensure_account_columns()


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
