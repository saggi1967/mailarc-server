"""ORM-Modelle des zentralen Bestands.

`mailbox` + `email` spiegeln das SQLite-Schema des Clients (app/db.py) 1:1 — inkl.
Idempotenz über UNIQUE(mailbox_id, uidvalidity, uid). `account` hält die zentral
verschlüsselten IMAP-Zugänge. `sync_job`/`sync_staged_email` sind der durable
Staging-Bereich für die asynchrone Sync-Verarbeitung (Vorschlag Abschnitt 6),
`sync_lock` der Per-Ordner-Advisory-Lock (Abschnitt 5).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class WebUser(Base):
    """Benutzer der client-API (Web-UI). Passwort gehasht, Rolle admin|user."""

    __tablename__ = "web_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="user")  # "admin" | "user"
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Account(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    imap_host: Mapped[str] = mapped_column(String(255))
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    imap_ssl: Mapped[bool] = mapped_column(default=True)
    imap_ssl_verify: Mapped[bool] = mapped_column(default=True)
    imap_user: Mapped[str] = mapped_column(String(255))
    imap_password_enc: Mapped[str] = mapped_column(Text)  # Fernet-Token, nie im Klartext
    folders: Mapped[str] = mapped_column(String(1024), default="INBOX")

    # Zentrale Client-Konfiguration je Profil (optional). NULL = der Client behält
    # seinen lokalen Default/Bootstrap-Wert. So muss nur ES_PASSWORD verschlüsselt
    # gehalten werden (es_password_enc), der Rest ist unkritische Betriebskonfig.
    es_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    es_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    es_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet
    es_index: Mapped[str | None] = mapped_column(String(255), nullable=True)
    es_verify_certs: Mapped[bool | None] = mapped_column(nullable=True)
    attachment_text: Mapped[bool | None] = mapped_column(nullable=True)
    attachment_max_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attachment_max_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Mailbox(Base):
    __tablename__ = "mailbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    uidvalidity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_uid: Mapped[int] = mapped_column(BigInteger, default=0)
    last_import_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Email(Base):
    __tablename__ = "email"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "uidvalidity", "uid", name="uq_email_ref"),
        Index("ix_email_message_id", "message_id"),
        Index("ix_email_es_pending", "es_indexed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailbox.id"), index=True)
    uid: Mapped[int] = mapped_column(BigInteger)
    uidvalidity: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[str | None] = mapped_column(String(998), nullable=True)
    from_addr: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_addr: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_header: Mapped[str | None] = mapped_column(String(128), nullable=True)
    internaldate: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    raw: Mapped[bytes] = mapped_column(LargeBinary)
    imported_at: Mapped[str] = mapped_column(String(64))
    es_indexed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SyncJob(Base):
    __tablename__ = "sync_job"

    tx_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    mailbox_name: Mapped[str] = mapped_column(String(1024))
    uidvalidity: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="accepted")  # accepted|running|done|failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SavedSearch(Base):
    """Favorit: eine benannte, serialisierte Filtermenge eines Web-Benutzers.

    ``params`` ist dieselbe Filterstruktur wie die Query-Parameter von
    ``/api/search`` — es entsteht keine neue Suchlogik, nur Persistenz. Benutzer-
    gebunden über ``owner_id``; Namen sind je Benutzer eindeutig.
    (Feature „Favoritenverwaltung", Phase F1 – Speichern & CRUD.)
    """

    __tablename__ = "saved_search"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_saved_search_owner_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("web_user.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SyncStagedEmail(Base):
    """Durables Staging: Upload überlebt hier, Verarbeitung läuft entkoppelt."""

    __tablename__ = "sync_staged_email"

    id: Mapped[int] = mapped_column(primary_key=True)
    tx_id: Mapped[str] = mapped_column(
        ForeignKey("sync_job.tx_id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[dict] = mapped_column(JSON)


class SyncLock(Base):
    """Advisory-Lock pro Ordner als Lease mit TTL (läuft automatisch ab)."""

    __tablename__ = "sync_lock"

    mailbox_name: Mapped[str] = mapped_column(String(1024), primary_key=True)
    lease_id: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class IndexJob(Base):
    """Asynchroner Elasticsearch-Indexlauf. Der Server baut die ES-Dokumente aus
    den Roh-Mails und schreibt sie nach ES (dort, wo ES erreichbar ist) — der Client
    stößt den Job nur an und pollt den Fortschritt, analog zu ``SyncJob``.
    """

    __tablename__ = "index_job"

    tx_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reindex: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16), default="accepted")  # accepted|running|done|failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    indexed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
