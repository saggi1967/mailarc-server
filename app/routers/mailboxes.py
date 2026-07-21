"""Ordner-Sync-Stand und Per-Ordner-Advisory-Lock.

Gegenstelle zu den mailbox-Methoden in ``app/storage/rest.py``. Das Wasserzeichen
``last_uid`` wird nur vorwärts bewegt (MAX), damit paralleler Sync desselben
Ordners datensicher bleibt (Vorschlag Abschnitt 5.1).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session, utcnow
from app.models import Email, Mailbox, SyncLock
from app.schemas import MailboxCreate, MailboxOut, MailboxPatch

router = APIRouter(prefix="/mailboxes", tags=["mailboxes"])


@router.get("", response_model=None)
def list_mailboxes(
    with_counts: int = Query(default=0),
    db: Session = Depends(get_session),
) -> list[dict]:
    if with_counts:
        rows = db.execute(
            select(
                Mailbox.name,
                Mailbox.uidvalidity,
                Mailbox.last_uid,
                Mailbox.last_import_at,
                func.count(Email.id).label("cnt"),
            )
            .outerjoin(Email, Email.mailbox_id == Mailbox.id)
            .group_by(Mailbox.id)
            .order_by(Mailbox.name)
        ).all()
        return [
            {
                "name": r.name,
                "uidvalidity": r.uidvalidity,
                "last_uid": r.last_uid,
                "last_import_at": r.last_import_at,
                "cnt": r.cnt,
            }
            for r in rows
        ]
    rows = db.scalars(select(Mailbox).order_by(Mailbox.name)).all()
    return [
        {
            "id": m.id,
            "name": m.name,
            "uidvalidity": m.uidvalidity,
            "last_uid": m.last_uid,
            "last_import_at": m.last_import_at,
        }
        for m in rows
    ]


@router.get("/{name}", response_model=MailboxOut)
def get_mailbox(name: str, db: Session = Depends(get_session)) -> Mailbox:
    mb = db.scalar(select(Mailbox).where(Mailbox.name == name))
    if mb is None:
        raise HTTPException(status_code=404, detail=f"Ordner '{name}' nicht gefunden.")
    return mb


@router.post("")
def upsert_mailbox(body: MailboxCreate, db: Session = Depends(get_session)) -> dict:
    mb = db.scalar(select(Mailbox).where(Mailbox.name == body.name))
    if mb is None:
        mb = Mailbox(name=body.name)
        db.add(mb)
        db.flush()
    return {"id": mb.id}


@router.patch("/{mailbox_id}")
def patch_mailbox(
    mailbox_id: int, body: MailboxPatch, db: Session = Depends(get_session)
) -> dict:
    mb = db.get(Mailbox, mailbox_id)
    if mb is None:
        raise HTTPException(status_code=404, detail="Ordner nicht gefunden.")

    if body.reset == "state":
        # UIDVALIDITY-Wechsel: alte UIDs ungültig, Stand zurück auf 0.
        mb.uidvalidity = body.uidvalidity
        mb.last_uid = 0
    elif body.reset == "full":
        # Voll-Import: Stand komplett verwerfen, UIDVALIDITY noch unbekannt.
        mb.uidvalidity = None
        mb.last_uid = 0
    else:
        if body.uidvalidity is not None:
            mb.uidvalidity = body.uidvalidity
        if body.last_uid is not None:
            # Wasserzeichen nur vorwärts.
            mb.last_uid = max(mb.last_uid or 0, body.last_uid)
        if body.last_import_at is not None:
            mb.last_import_at = body.last_import_at
    return {"ok": True}


# ── Advisory-Lock pro Ordner (Effizienz-Schicht, Vorschlag 5.2/5.3) ──────────
@router.post("/{name}/sync-lock")
def acquire_lock(name: str, db: Session = Depends(get_session)):
    now = utcnow()
    lock = db.get(SyncLock, name)
    if lock is not None and lock.expires_at > now:
        return JSONResponse(
            status_code=409,
            content={"locked_until": lock.expires_at.isoformat()},
        )
    lease_id = uuid.uuid4().hex
    expires = now + timedelta(seconds=settings.LOCK_TTL_SECONDS)
    if lock is None:
        db.add(SyncLock(mailbox_name=name, lease_id=lease_id, expires_at=expires))
    else:
        lock.lease_id = lease_id
        lock.expires_at = expires
    return {"lease_id": lease_id, "expires_at": expires.isoformat()}


@router.delete("/{name}/sync-lock")
def release_lock(name: str, db: Session = Depends(get_session)) -> dict:
    lock = db.get(SyncLock, name)
    if lock is not None:
        db.delete(lock)
    return {"released": True}
