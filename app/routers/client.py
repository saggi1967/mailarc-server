"""client-API (``/api``) für Web-UI und künftige Rich-Clients.

Bewusst client-neutral: Session-Cookie-Auth (Browser-freundlich, kein Token im JS),
Suche als Server-Proxy vor Elasticsearch (ES wird nie an den Client exponiert),
Einzelmail-Detail aus dem ES-Dokument. Getrennt vom internen CLI-Vertrag
(``/accounts``, ``/sync-jobs`` …), der weiter das statische Bearer-Token nutzt.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app import es
from app.config import settings
from app.webauth import COOKIE_NAME, check_login, current_user, issue_token

router = APIRouter(prefix="/api", tags=["client"])

_TOP_FIELDS = {"from_domain", "from_addr", "mailbox"}


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
