"""Mails lesen: Index-Paginierung, mark-indexed, Roh-Bytes-Ausgabe.

Gegenstelle zu den Lese-/Index-Methoden in ``app/storage/rest.py``. Der Index-Lauf
zieht die Roh-Bytes paginiert über ``cursor`` (streaming statt alles auf einmal).
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.models import Email, Mailbox
from app.schemas import MarkIndexedIn

router = APIRouter(prefix="/emails", tags=["emails"])


@router.get("/count")
def count_pending(reindex: bool = Query(default=False), db: Session = Depends(get_session)) -> dict:
    q = select(func.count(Email.id))
    if not reindex:
        q = q.where(Email.es_indexed_at.is_(None))
    return {"count": db.scalar(q) or 0}


@router.get("")
def list_for_index(
    index_pending: int = Query(default=0),
    reindex: bool = Query(default=False),
    cursor: int = Query(default=0),
    limit: int = Query(default=200),
    db: Session = Depends(get_session),
) -> dict:
    limit = max(1, min(limit, settings.MAX_PAGE_LIMIT))
    q = (
        select(Email, Mailbox.name)
        .join(Mailbox, Mailbox.id == Email.mailbox_id)
        .where(Email.id > cursor)
    )
    if not reindex:
        q = q.where(Email.es_indexed_at.is_(None))
    q = q.order_by(Email.id).limit(limit)

    rows = db.execute(q).all()
    items = [
        {
            "id": e.id,
            "mailbox": mailbox_name,
            "uid": e.uid,
            "uidvalidity": e.uidvalidity,
            "internaldate": e.internaldate,
            "size": e.size,
            "raw_base64": base64.b64encode(e.raw).decode("ascii"),
        }
        for e, mailbox_name in rows
    ]
    next_cursor = items[-1]["id"] if len(items) == limit else None
    return {"items": items, "next_cursor": next_cursor}


@router.patch("/mark-indexed")
def mark_indexed(body: MarkIndexedIn, db: Session = Depends(get_session)) -> dict:
    if body.ids:
        db.execute(
            update(Email).where(Email.id.in_(body.ids)).values(es_indexed_at=body.indexed_at)
        )
    return {"updated": len(body.ids)}


def _raw_response(raw: bytes) -> Response:
    return Response(content=raw, media_type="application/octet-stream")


@router.get("/by-message-id/{message_id}/raw")
def raw_by_message_id(message_id: str, db: Session = Depends(get_session)) -> Response:
    e = db.scalar(select(Email).where(Email.message_id == message_id))
    if e is None:
        raise HTTPException(status_code=404, detail="Mail nicht gefunden.")
    return _raw_response(e.raw)


@router.get("/{mailbox}/{uidvalidity}/{uid}/raw")
def raw_by_ref(
    mailbox: str, uidvalidity: int, uid: int, db: Session = Depends(get_session)
) -> Response:
    e = db.scalar(
        select(Email)
        .join(Mailbox, Mailbox.id == Email.mailbox_id)
        .where(Mailbox.name == mailbox, Email.uidvalidity == uidvalidity, Email.uid == uid)
    )
    if e is None:
        raise HTTPException(status_code=404, detail="Mail nicht gefunden.")
    return _raw_response(e.raw)
