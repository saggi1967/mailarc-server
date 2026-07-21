"""Pydantic-Request/Response-Modelle — spiegeln exakt den Client-Vertrag.

Feldnamen und -typen entsprechen dem, was ``app/accounts.py`` und
``app/storage/rest.py`` des imap-archivers senden bzw. erwarten.
"""

from __future__ import annotations

from pydantic import BaseModel


# ── Accounts ────────────────────────────────────────────────────────────────
class AccountCreate(BaseModel):
    name: str
    imap_host: str
    imap_port: int = 993
    imap_ssl: bool = True
    imap_ssl_verify: bool = True
    imap_user: str
    imap_password: str
    folders: str = "INBOX"


class AccountUpdate(BaseModel):
    """Teil-Update (PATCH): nur gesetzte Felder werden geändert."""

    imap_host: str | None = None
    imap_port: int | None = None
    imap_ssl: bool | None = None
    imap_ssl_verify: bool | None = None
    imap_user: str | None = None
    imap_password: str | None = None
    folders: str | None = None


class AccountOut(BaseModel):
    """Liste/Anzeige — ohne Passwort."""

    name: str
    imap_host: str
    imap_port: int
    imap_ssl: bool
    imap_ssl_verify: bool
    imap_user: str
    folders: str


class CredentialsOut(BaseModel):
    """Vom Client vor dem IMAP-Zugriff geladen — enthält das Klartext-Passwort."""

    imap_host: str
    imap_port: int
    imap_ssl: bool
    imap_ssl_verify: bool
    imap_user: str
    imap_password: str
    folders: str


# ── Mailboxes ───────────────────────────────────────────────────────────────
class MailboxCreate(BaseModel):
    name: str


class MailboxPatch(BaseModel):
    reset: str | None = None  # "state" | "full"
    uidvalidity: int | None = None
    last_uid: int | None = None
    last_import_at: str | None = None


class MailboxOut(BaseModel):
    id: int
    name: str
    uidvalidity: int | None = None
    last_uid: int = 0
    last_import_at: str | None = None


# ── Emails / Sync-Jobs ──────────────────────────────────────────────────────
class EmailIn(BaseModel):
    uid: int
    uidvalidity: int
    message_id: str | None = None
    from_addr: str | None = None
    to_addr: str | None = None
    subject: str | None = None
    date_header: str | None = None
    internaldate: str | None = None
    size: int | None = None
    raw_base64: str


class SyncJobIn(BaseModel):
    idempotency_key: str
    mailbox_name: str
    uidvalidity: int | None = None
    last_uid: int | None = None
    emails: list[EmailIn]


class MarkIndexedIn(BaseModel):
    ids: list[int]
    indexed_at: str
