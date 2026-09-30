"""mailarc-server — zentrale Gegenstelle zum imap-archiver-Client.

Startet die FastAPI-App, legt beim Start das Schema an und bindet alle Router
hinter Bearer-Auth ein. Start:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from elastic_transport import ConnectionError as ESConnectionError
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__, webusers
from app.config import settings
from app.db import SessionLocal, init_db
from app.routers import (
    accounts,
    client,
    emails,
    es_query,
    index_jobs,
    mailboxes,
    stats,
    sync_jobs,
)
from app.security import require_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Ersten Admin aus WEB_USERNAME/WEB_PASSWORD anlegen, falls noch keine Benutzer.
    with SessionLocal() as db:
        webusers.ensure_seed_admin(db)
    yield


app = FastAPI(title="mailarc-server", version=__version__, lifespan=lifespan)

# CORS für das getrennte Frontend (mailarc-web). Credentials nötig, da die
# client-API mit httpOnly-Session-Cookie arbeitet.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.web_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ESConnectionError)
async def _es_unreachable(_request: Request, _exc: ESConnectionError) -> JSONResponse:
    """Elasticsearch nicht erreichbar → sauberes 503 statt 500-Stacktrace.

    Typische Ursache im Docker-Betrieb: ``ES_HOST=localhost`` zeigt auf den
    Container selbst. Für ES auf dem Host ``host.docker.internal`` verwenden.
    """
    return JSONResponse(
        status_code=503,
        content={"detail": "Suchindex (Elasticsearch) nicht erreichbar."},
    )


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


# Interner CLI-Vertrag: alle Router erfordern ein gültiges Bearer-Token.
for module in (accounts, mailboxes, emails, sync_jobs, index_jobs, es_query, stats):
    app.include_router(module.router, dependencies=[Depends(require_token)])

# client-API (/api): eigene Session-Cookie-Auth statt Bearer-Token.
app.include_router(client.router)

# ── Web-Frontend (mailarc-web) same-origin ausliefern ───────────────────────
# Das gebaute SPA liegt unter <repo>/web (ins Image kopiert). Same-origin: die API
# bleibt unter /api, das UI unter / — so sind die Session-Cookies first-party
# (SameSite=Lax, ohne HTTPS/CORS). Muss NACH allen Routern registriert werden,
# damit /api & Co. Vorrang vor dem SPA-Fallback haben.
_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
# Für diese Präfixe KEIN index.html-Fallback (echtes 404 statt SPA-Seite).
_API_PREFIXES = {
    "api", "accounts", "mailboxes", "emails", "sync-jobs", "index-jobs",
    "es", "stats", "health", "docs", "redoc", "openapi.json",
}

if os.path.isdir(_WEB_DIR):
    app.mount(
        "/assets", StaticFiles(directory=os.path.join(_WEB_DIR, "assets")), name="assets"
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        """SPA-Auslieferung: echte Dateien direkt, alle App-Routen → index.html.

        React Router macht clientseitiges Routing; ein Reload auf z. B. /search
        muss daher index.html liefern statt 404.
        """
        if full_path.split("/", 1)[0] in _API_PREFIXES:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = os.path.normpath(os.path.join(_WEB_DIR, full_path))
        if (
            full_path
            and candidate.startswith(_WEB_DIR + os.sep)
            and os.path.isfile(candidate)
        ):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_WEB_DIR, "index.html"))
