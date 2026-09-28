"""Elasticsearch-Zugriff für die client-API.

Der Server spricht ES direkt (gleiche Instanz/Index wie der CLI-Indexlauf), damit
Web-/Rich-Clients suchen können, ohne dass ES je an den Browser exponiert wird. Die
Query-Logik ist bewusst identisch zur CLI (``app/commands/search.py`` im Client),
damit Suche über CLI und API dieselben Treffer liefert.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

from elasticsearch import Elasticsearch

from app.config import settings


# Suchoptimales Mapping — identisch zum bisherigen CLI-Index (imap-archiver
# app/es.py), damit die zentrale Indexierung dieselben Felder/Analyzer erzeugt.
INDEX_BODY = {
    "settings": {
        "analysis": {
            "normalizer": {
                "lower": {"type": "custom", "filter": ["lowercase"]},
            }
        }
    },
    "mappings": {
        "properties": {
            "mailbox": {"type": "keyword"},
            "uid": {"type": "long"},
            "uidvalidity": {"type": "long"},
            "message_id": {"type": "keyword"},
            "subject": {
                "type": "text",
                "analyzer": "german",
                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
            },
            "body": {"type": "text", "analyzer": "german"},
            "attachment_text": {"type": "text", "analyzer": "german"},
            "from_addr": {"type": "keyword", "normalizer": "lower"},
            "from_name": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
            },
            "from_domain": {"type": "keyword", "normalizer": "lower"},
            "to": {"type": "keyword", "normalizer": "lower"},
            "cc": {"type": "keyword", "normalizer": "lower"},
            "date": {"type": "date"},
            "internaldate": {"type": "date"},
            "size": {"type": "long"},
            "has_attachment": {"type": "boolean"},
            "attachment_count": {"type": "integer"},
            "attachments": {
                "type": "nested",
                "properties": {
                    "filename": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                    },
                    "content_type": {"type": "keyword"},
                    "size": {"type": "long"},
                    "has_text": {"type": "boolean"},
                },
            },
        }
    },
}


@lru_cache
def client() -> Elasticsearch:
    kwargs: dict = {"basic_auth": (settings.ES_USER, settings.ES_PASSWORD)}
    if settings.ES_HOST.lower().startswith("https"):
        kwargs["verify_certs"] = settings.ES_VERIFY_CERTS
        kwargs["ssl_show_warn"] = settings.ES_VERIFY_CERTS
    return Elasticsearch(settings.ES_HOST, **kwargs)


def ensure_index(es: Elasticsearch, index: str) -> bool:
    """Legt den Index mit Mapping an, falls er fehlt. True = neu erstellt."""
    if es.indices.exists(index=index):
        return False
    es.indices.create(index=index, **INDEX_BODY)
    return True


def sync_mapping(es: Elasticsearch, index: str) -> None:
    """Fügt neue Felder additiv zum bestehenden Mapping hinzu (idempotent)."""
    es.indices.put_mapping(index=index, properties=INDEX_BODY["mappings"]["properties"])


def parse_last(last: str) -> str:
    """'7d' / '24h' / '30m' / '2w' → ISO-Zeitpunkt 'jetzt minus X'."""
    units = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    unit = last[-1].lower()
    if unit not in units or not last[:-1].isdigit():
        raise ValueError("Format: z. B. 24h, 7d, 30m, 2w")
    return (datetime.now() - timedelta(**{units[unit]: int(last[:-1])})).isoformat()


def build_query(
    text: str | None = None,
    frm: str | None = None,
    to: str | None = None,
    domain: str | None = None,
    subject: str | None = None,
    file: str | None = None,
    mailbox: str | None = None,
    has_attachment: bool | None = None,
    since: str | None = None,
    until: str | None = None,
    phrase: bool = False,
) -> dict:
    must: list[dict] = []
    filt: list[dict] = []

    if text:
        must.append(
            {
                "multi_match": {
                    "query": text,
                    "fields": ["subject^3", "from_name^2", "body", "attachment_text"],
                    "type": "phrase" if phrase else "best_fields",
                }
            }
        )
    if subject:
        must.append({"match": {"subject": subject}})
    if file:
        must.append(
            {
                "nested": {
                    "path": "attachments",
                    "query": {"match": {"attachments.filename": file}},
                }
            }
        )
    if frm:
        filt.append({"term": {"from_addr": frm.lower()}})
    if to:
        filt.append({"term": {"to": to.lower()}})
    if domain:
        filt.append({"term": {"from_domain": domain.lower()}})
    if mailbox:
        filt.append({"term": {"mailbox": mailbox}})
    if has_attachment is not None:
        filt.append({"term": {"has_attachment": has_attachment}})

    rng: dict = {}
    if since:
        rng["gte"] = since
    if until:
        rng["lte"] = until
    if rng:
        filt.append({"range": {"date": rng}})

    if not must and not filt:
        return {"match_all": {}}
    return {"bool": {"must": must or [{"match_all": {}}], "filter": filt}}
