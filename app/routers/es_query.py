"""Such-Proxy für den CLI-Vertrag: führt Elasticsearch-`search`/`count` server-seitig aus.

Gegenstelle zu ``RestStorage.es_search``/``es_count`` im Client. Seit die CLI kein
Elasticsearch mehr direkt anspricht (ES ist nur auf dem Server-Host erreichbar),
baut der Client die Query wie gewohnt und schickt sie hierher; der Server reicht sie
read-only an ES weiter. Bearer-geschützt (interner CLI-Vertrag), fest auf den
konfigurierten Index (``ES_INDEX``) gescoped — ein Client kann keinen anderen Index
oder andere Operationen erreichen.
"""

from __future__ import annotations

from fastapi import APIRouter, Body

from app import es
from app.config import settings

router = APIRouter(prefix="/es", tags=["es-query"])

# Nur diese Such-Parameter werden an ES durchgereicht (Namen wie in elasticsearch-py
# ``search()``). Alles andere aus dem Body wird ignoriert.
_ALLOWED = {
    "query",
    "size",
    "from_",
    "sort",
    "source_includes",
    "source_excludes",
    "highlight",
    "aggs",
    "aggregations",
    "track_total_hits",
}


@router.post("/search")
def es_search(body: dict = Body(...)) -> dict:
    kwargs = {k: v for k, v in body.items() if k in _ALLOWED}
    resp = es.client().search(index=settings.ES_INDEX, **kwargs)
    return resp.body


@router.post("/count")
def es_count(body: dict = Body(...)) -> dict:
    query = body.get("query")
    resp = es.client().count(index=settings.ES_INDEX, query=query)
    return {"count": resp["count"]}
