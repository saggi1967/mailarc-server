"""client-API für Favoriten / gespeicherte Suchen (Feature F1 – Speichern & CRUD).

Ein Favorit (``saved_search``) ist eine benannte, serialisierte Filtermenge — exakt
die Parameter von ``/api/search``. Hier liegt nur Persistenz, CRUD und die Sofort-
Ausführung (``run``); es entsteht keine neue Suchlogik: ``run`` nutzt denselben
Server-Proxy (``execute_search``) wie ``/api/search``. Favoriten sind benutzer-
gebunden (Scope über die Web-Session), Sichtbarkeit ist in F1 implizit privat.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import mql
from app.db import get_session
from app.models import SavedSearch, WebUser
from app.routers.client import execute_search, run_es_query
from app.schemas import (
    SavedSearchCreate,
    SavedSearchOut,
    SavedSearchParams,
    SavedSearchUpdate,
)
from app.webauth import current_web_user

router = APIRouter(prefix="/api/searches", tags=["searches"])


def _out(s: SavedSearch) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "params": s.params or {},
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def _owned_or_404(db: Session, owner_id: int, search_id: int) -> SavedSearch:
    """Lädt den Favoriten NUR, wenn er dem angemeldeten Benutzer gehört.

    Fremde oder nicht existierende IDs ergeben identisch 404 — so verrät die API
    nicht, ob eine fremde ID existiert.
    """
    s = db.scalar(
        select(SavedSearch).where(
            SavedSearch.id == search_id, SavedSearch.owner_id == owner_id
        )
    )
    if s is None:
        raise HTTPException(status_code=404, detail=f"Favorit {search_id} nicht gefunden.")
    return s


@router.get("", response_model=list[SavedSearchOut])
def list_searches(
    db: Session = Depends(get_session), user: WebUser = Depends(current_web_user)
) -> list[dict]:
    rows = db.scalars(
        select(SavedSearch)
        .where(SavedSearch.owner_id == user.id)
        .order_by(SavedSearch.name)
    ).all()
    return [_out(s) for s in rows]


@router.post("", response_model=SavedSearchOut, status_code=201)
def create_search(
    body: SavedSearchCreate,
    db: Session = Depends(get_session),
    user: WebUser = Depends(current_web_user),
) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name darf nicht leer sein.")
    s = SavedSearch(
        owner_id=user.id,
        name=name,
        params=body.params.model_dump(by_alias=True, exclude_none=True),
    )
    db.add(s)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Favorit '{name}' existiert bereits."
        ) from exc
    return _out(s)


@router.patch("/{search_id}", response_model=SavedSearchOut)
def update_search(
    search_id: int,
    body: SavedSearchUpdate,
    db: Session = Depends(get_session),
    user: WebUser = Depends(current_web_user),
) -> dict:
    s = _owned_or_404(db, user.id, search_id)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Name darf nicht leer sein.")
        s.name = name
    if body.params is not None:
        s.params = body.params.model_dump(by_alias=True, exclude_none=True)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Ein Favorit mit diesem Namen existiert bereits."
        ) from exc
    return _out(s)


@router.delete("/{search_id}")
def delete_search(
    search_id: int,
    db: Session = Depends(get_session),
    user: WebUser = Depends(current_web_user),
) -> dict:
    s = _owned_or_404(db, user.id, search_id)
    db.delete(s)
    return {"deleted": search_id}


@router.post("/{search_id}/run")
def run_search(
    search_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
    user: WebUser = Depends(current_web_user),
) -> dict:
    """Führt den Favoriten sofort aus und liefert Treffer wie ``/api/search``.

    ``limit``/``offset`` sind Lauf-Parameter (Pagination) und werden nicht im
    Favoriten gespeichert.
    """
    s = _owned_or_404(db, user.id, search_id)
    p = SavedSearchParams.model_validate(s.params or {})
    if p.mql:
        # MQL-Favorit: über den Parser ausführen (gleiche Ausführungsstelle wie /api/search/mql).
        try:
            query = mql.compile_query(p.mql)
        except mql.MqlError as exc:
            raise HTTPException(
                status_code=422, detail={"message": exc.message, "position": exc.position}
            ) from exc
        return run_es_query(query, limit, offset)
    return execute_search(
        q=p.q, frm=p.from_, to=p.to, domain=p.domain, subject=p.subject,
        phrase=p.phrase, file=p.file, mailbox=p.mailbox, attachments=p.attachments,
        since=p.since, until=p.until, last=p.last, limit=limit, offset=offset,
    )
