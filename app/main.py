"""mailarc-server — zentrale Gegenstelle zum imap-archiver-Client.

Startet die FastAPI-App, legt beim Start das Schema an und bindet alle Router
hinter Bearer-Auth ein. Start:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.db import init_db
from app.routers import accounts, emails, mailboxes, stats, sync_jobs
from app.security import require_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


from app import __version__

app = FastAPI(title="mailarc-server", version=__version__, lifespan=lifespan)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


# Alle fachlichen Router erfordern ein gültiges Bearer-Token.
for module in (accounts, mailboxes, emails, sync_jobs, stats):
    app.include_router(module.router, dependencies=[Depends(require_token)])
