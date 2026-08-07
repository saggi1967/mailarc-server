"""mailarc-server — zentrale Gegenstelle zum imap-archiver-Client.

Startet die FastAPI-App, legt beim Start das Schema an und bindet alle Router
hinter Bearer-Auth ein. Start:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import settings
from app.db import init_db
from app.routers import accounts, client, emails, mailboxes, stats, sync_jobs
from app.security import require_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


# Interner CLI-Vertrag: alle Router erfordern ein gültiges Bearer-Token.
for module in (accounts, mailboxes, emails, sync_jobs, stats):
    app.include_router(module.router, dependencies=[Depends(require_token)])

# client-API (/api): eigene Session-Cookie-Auth statt Bearer-Token.
app.include_router(client.router)
