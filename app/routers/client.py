"""client-API (``/api``) für Web-UI und künftige Rich-Clients.

Bewusst client-neutral: Session-Cookie-Auth (Browser-freundlich, kein Token im JS),
Suche als Server-Proxy vor Elasticsearch (ES wird nie an den Client exponiert),
Einzelmail-Detail aus dem ES-Dokument. Getrennt vom internen CLI-Vertrag
(``/accounts``, ``/sync-jobs`` …), der weiter das statische Bearer-Token nutzt.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import es, mailparse, render
from app.config import settings
from app.db import get_session
from app.models import Email, Mailbox
from app.routers import accounts as accounts_router
from app.routers.stats import compute_summary
from app.schemas import AccountCreate, AccountOut, AccountUpdate
from app.webauth import COOKIE_NAME, check_login, current_user, issue_token

router = APIRouter(prefix="/api", tags=["client"])

_TOP_FIELDS = {"from_domain", "from_addr", "mailbox"}


def _email_by_doc_id(db: Session, doc_id: str) -> Email | None:
    """Löst mailbox:uidvalidity:uid oder Message-ID zur Mail in der zentralen DB."""
    parts = doc_id.rsplit(":", 2)
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        e = db.scalar(
            select(Email)
            .join(Mailbox, Mailbox.id == Email.mailbox_id)
            .where(Mailbox.name == parts[0], Email.uidvalidity == int(parts[1]), Email.uid == int(parts[2]))
        )
        if e is not None:
            return e
    return db.scalar(select(Email).where(Email.message_id == doc_id))


def _safe_name(name: str | None, idx: int) -> str:
    base = re.sub(r"[^\w.\-() ]", "_", (name or "").rsplit("/", 1)[-1]).strip(" .")
    return base or f"anhang-{idx}"


def _attachments(raw: bytes) -> list[tuple[str | None, str, bytes]]:
    return [(n, c, d) for (n, c, d) in mailparse.iter_attachments(raw) if d]


# ── Auth ─────────────────────────────────────────────────────────────────────
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, response: Response) -> dict:
    if not check_login(body.username, body.password):
        raise HTTPException(status_code=401, detail="Ungültige Zugangsdaten.")
    response.set_cookie(
        COOKIE_NAME,
        issue_token(body.username),
        max_age=settings.WEB_SESSION_TTL,
        httponly=True,
        secure=settings.WEB_COOKIE_SECURE,
        samesite=settings.WEB_COOKIE_SAMESITE,
        path="/",
    )
    return {"user": body.username}


@router.get("/auth/me")
def me(user: str = Depends(current_user)) -> dict:
    return {"user": user}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    # Bewusst ohne Auth-Zwang: ein abgelaufenes Cookie soll sich trotzdem löschen lassen.
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


# ── Suche ────────────────────────────────────────────────────────────────────
def _since_iso(since: str | None, last: str | None) -> str | None:
    if last:
        try:
            return es.parse_last(last)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return since


def _hit_to_item(h: dict) -> dict:
    s = h.get("_source", {})
    hl = h.get("highlight", {})
    snippet = (hl.get("body") or hl.get("attachment_text") or [None])[0]
    return {
        "id": h["_id"],
        "date": s.get("date"),
        "from_addr": s.get("from_addr"),
        "from_name": s.get("from_name"),
        "subject": s.get("subject"),
        "mailbox": s.get("mailbox"),
        "uid": s.get("uid"),
        "uidvalidity": s.get("uidvalidity"),
        "has_attachment": s.get("has_attachment"),
        "attachment_count": s.get("attachment_count"),
        "snippet": snippet,
    }


@router.get("/search")
def search(
    q: str | None = None,
    frm: str | None = Query(None, alias="from"),
    to: str | None = None,
    domain: str | None = None,
    subject: str | None = None,
    phrase: bool = False,
    file: str | None = None,
    mailbox: str | None = None,
    attachments: bool | None = None,
    since: str | None = None,
    until: str | None = None,
    last: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: str = Depends(current_user),
) -> dict:
    """Volltextsuche als Server-Proxy vor Elasticsearch (Treffer, paginiert)."""
    query = es.build_query(
        q, frm, to, domain, subject, file, mailbox, attachments,
        _since_iso(since, last), until, phrase,
    )
    resp = es.client().search(
        index=settings.ES_INDEX,
        query=query,
        size=limit,
        from_=offset,
        sort=[{"date": {"order": "desc", "missing": "_last"}}],
        highlight={
            "fields": {
                "body": {"fragment_size": 150, "number_of_fragments": 1},
                "attachment_text": {"fragment_size": 150, "number_of_fragments": 1},
            }
        },
        source_excludes=["body", "attachment_text"],
    )
    hits = resp["hits"]["hits"]
    return {
        "total": resp["hits"]["total"]["value"],
        "count": len(hits),
        "offset": offset,
        "items": [_hit_to_item(h) for h in hits],
    }


@router.get("/search/count")
def count(
    q: str | None = None,
    frm: str | None = Query(None, alias="from"),
    domain: str | None = None,
    mailbox: str | None = None,
    since: str | None = None,
    until: str | None = None,
    last: str | None = None,
    phrase: bool = False,
    user: str = Depends(current_user),
) -> dict:
    query = es.build_query(
        q, frm, None, domain, None, None, mailbox, None, _since_iso(since, last), until, phrase
    )
    n = es.client().count(index=settings.ES_INDEX, query=query)["count"]
    return {"count": n}


@router.get("/search/top")
def top(
    by: str = "from_domain",
    size: int = Query(15, ge=1, le=100),
    user: str = Depends(current_user),
) -> dict:
    if by not in _TOP_FIELDS:
        raise HTTPException(status_code=422, detail=f"by muss aus {sorted(_TOP_FIELDS)} sein.")
    resp = es.client().search(
        index=settings.ES_INDEX, size=0, aggs={"grp": {"terms": {"field": by, "size": size}}}
    )
    buckets = resp["aggregations"]["grp"]["buckets"]
    return {"by": by, "buckets": [{"key": b["key"], "count": b["doc_count"]} for b in buckets]}


# ── Einzelmail ───────────────────────────────────────────────────────────────
@router.get("/emails/{doc_id}")
def email_detail(doc_id: str, user: str = Depends(current_user)) -> dict:
    """Vollständige Mail (ES-Dokument): Header, Body, Anhang-Metadaten.

    ``doc_id`` = ``mailbox:uidvalidity:uid`` oder Message-ID.
    """
    c = es.client()
    if c.exists(index=settings.ES_INDEX, id=doc_id):
        return c.get(index=settings.ES_INDEX, id=doc_id)["_source"]
    resp = c.search(index=settings.ES_INDEX, query={"term": {"message_id": doc_id}}, size=1)
    hits = resp["hits"]["hits"]
    if not hits:
        raise HTTPException(status_code=404, detail=f"Keine Mail zu '{doc_id}' gefunden.")
    return hits[0]["_source"]


@router.get("/emails/{doc_id}/pdf")
def email_pdf(
    doc_id: str,
    load_remote: bool = False,
    db: Session = Depends(get_session),
    user: str = Depends(current_user),
) -> Response:
    """Rendert die Mail serverseitig als PDF (Quelle: Roh-Mail der zentralen DB)."""
    e = _email_by_doc_id(db, doc_id)
    if e is None:
        raise HTTPException(status_code=404, detail=f"Keine Mail zu '{doc_id}' gefunden.")
    if not render.WEASYPRINT_OK:
        raise HTTPException(
            status_code=503,
            detail=f"PDF-Rendering nicht verfügbar (WeasyPrint fehlt: {render.WEASYPRINT_ERROR}).",
        )
    pdf = render.html_to_pdf(e.raw, load_remote=load_remote)
    if pdf is None:
        raise HTTPException(status_code=422, detail="Mail hat keinen darstellbaren Inhalt.")
    fname = _safe_name(mailparse.subject_of(e.raw) or doc_id, 0)[:120] or "mail"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{fname}.pdf"'},
    )


@router.get("/emails/{doc_id}/attachments")
def email_attachments(
    doc_id: str,
    db: Session = Depends(get_session),
    user: str = Depends(current_user),
) -> dict:
    """Listet die Anhänge einer Mail (Index 1-basiert, stabil zum Download)."""
    e = _email_by_doc_id(db, doc_id)
    if e is None:
        raise HTTPException(status_code=404, detail=f"Keine Mail zu '{doc_id}' gefunden.")
    items = [
        {"index": i, "filename": name, "content_type": ctype, "size": len(data)}
        for i, (name, ctype, data) in enumerate(_attachments(e.raw), start=1)
    ]
    return {"count": len(items), "attachments": items}


@router.get("/emails/{doc_id}/attachments/{index}")
def email_attachment_download(
    doc_id: str,
    index: int,
    db: Session = Depends(get_session),
    user: str = Depends(current_user),
) -> Response:
    """Lädt einen einzelnen Anhang (1-basiert) herunter."""
    e = _email_by_doc_id(db, doc_id)
    if e is None:
        raise HTTPException(status_code=404, detail=f"Keine Mail zu '{doc_id}' gefunden.")
    items = _attachments(e.raw)
    if not 1 <= index <= len(items):
        raise HTTPException(
            status_code=404, detail=f"Anhang {index} ungültig — Mail hat {len(items)} Anhang/Anhänge."
        )
    name, ctype, data = items[index - 1]
    fname = _safe_name(name, index)
    return Response(
        content=data,
        media_type=ctype or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/stats/summary")
def stats_summary(
    top: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_session),
    user: str = Depends(current_user),
) -> dict:
    """Statistik fürs Dashboard (serverseitig aggregiert)."""
    return compute_summary(db, top)


# ── Kontenverwaltung (cookie-authentifiziert) ────────────────────────────────
# Reicht die getestete CRUD-Logik aus routers/accounts.py durch — hier aber hinter
# der Web-Session statt dem statischen Bearer-Token. Direktaufruf mit explizitem
# `db` umgeht das dortige Depends.
@router.get("/accounts", response_model=list[AccountOut])
def api_accounts_list(
    db: Session = Depends(get_session), user: str = Depends(current_user)
) -> list[dict]:
    return accounts_router.list_accounts(db=db)


@router.post("/accounts", response_model=AccountOut, status_code=201)
def api_accounts_create(
    body: AccountCreate, db: Session = Depends(get_session), user: str = Depends(current_user)
) -> dict:
    return accounts_router.create_account(body=body, db=db)


@router.patch("/accounts/{name}", response_model=AccountOut)
def api_accounts_update(
    name: str,
    body: AccountUpdate,
    db: Session = Depends(get_session),
    user: str = Depends(current_user),
) -> dict:
    return accounts_router.update_account(name=name, body=body, db=db)


@router.delete("/accounts/{name}")
def api_accounts_delete(
    name: str, db: Session = Depends(get_session), user: str = Depends(current_user)
) -> dict:
    return accounts_router.delete_account(name=name, db=db)
