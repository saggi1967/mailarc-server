"""Serverseitige Statistik-Aggregation (statt alle Zeilen zum Client zu ziehen).

Spiegelt exakt die Logik aus ``app/storage/sqlite.py::stats_summary`` des Clients:
Datum aus ``date_header`` (Fallback ``internaldate``), Absender per ``parseaddr``
kleingeschrieben, Größensumme, Top-N-Absender.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from email.utils import parseaddr

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Email

router = APIRouter(prefix="/stats", tags=["stats"])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        return None


def compute_summary(db: Session, top: int = 10) -> dict:
    """Aggregiert die Mail-Statistik. Wiederverwendbar (CLI-Vertrag + client-API)."""
    rows = db.execute(
        select(
            Email.date_header, Email.internaldate, Email.from_addr, Email.size
        )
    ).all()

    years: Counter[int] = Counter()
    months: Counter[int] = Counter()
    weekdays: Counter[int] = Counter()
    senders: Counter[str] = Counter()
    total_size = 0
    dts: list[datetime] = []

    for date_header, internaldate, from_addr, size in rows:
        dt = _parse_dt(date_header) or _parse_dt(internaldate)
        if dt:
            years[dt.year] += 1
            months[dt.month] += 1
            weekdays[dt.weekday()] += 1
            dts.append(dt)
        name, addr = parseaddr(from_addr or "")
        senders[(addr or name or "‹unbekannt›").lower()] += 1
        total_size += size or 0

    return {
        "total": len(rows),
        "total_size": total_size,
        "span_start": min(dts).isoformat() if dts else None,
        "span_end": max(dts).isoformat() if dts else None,
        "distinct_senders": len(senders),
        "per_year": dict(years),
        "per_month": dict(months),
        "per_weekday": dict(weekdays),
        "top_senders": [[name, cnt] for name, cnt in senders.most_common(top)],
    }


@router.get("/summary")
def summary(top: int = Query(default=10), db: Session = Depends(get_session)) -> dict:
    return compute_summary(db, top)
