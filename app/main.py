"""mailarc-server — zentrale Gegenstelle zum imap-archiver-Client.

Startet die FastAPI-App, legt beim Start das Schema an und bindet alle Router
hinter Bearer-Auth ein. Start:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from elastic_transport import ConnectionError as ESConnectionError
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__, webusers
from app.config import settings
from app.db import SessionLocal, init_db
from app.routers import accounts, client, emails, mailboxes, stats, sync_jobs
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
for module in (accounts, mailboxes, emails, sync_jobs, stats):
    app.include_router(module.router, dependencies=[Depends(require_token)])

# client-API (/api): eigene Session-Cookie-Auth statt Bearer-Token.
app.include_router(client.router)
