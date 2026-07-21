"""Asynchrone Sync-Jobs: Upload ins Staging (202) + Status-Poll.

Gegenstelle zu ``store_email_batch``/``_poll_job`` in ``app/storage/rest.py``.
Der POST schreibt den Batch durable ins Staging und gibt sofort ``tx_id`` zurück;
die eigentliche Übernahme in den Bestand läuft in ``jobs.process_sync_job``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.jobs import process_sync_job
from app.models import SyncJob, SyncStagedEmail
from app.schemas import SyncJobIn

router = APIRouter(prefix="/sync-jobs", tags=["sync-jobs"])


def _job_out(job: SyncJob) -> dict:
    return {
        "tx_id": job.tx_id,
        "status": job.status,
        "total": job.total,
        "processed": job.processed,
        "inserted": job.inserted,
        "skipped": job.skipped,
        "errors": job.errors or [],
    }


@router.post("")
def create_sync_job(
    body: SyncJobIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    # Idempotenter Retry: gleicher Key → bestehenden Job zurückgeben, nicht duplizieren.
    existing = db.scalar(
        select(SyncJob).where(SyncJob.idempotency_key == body.idempotency_key)
    )
    if existing is not None:
        return JSONResponse(
            status_code=202, content={"tx_id": existing.tx_id, "status": existing.status}
        )

    tx_id = uuid.uuid4().hex
    job = SyncJob(
        tx_id=tx_id,
        idempotency_key=body.idempotency_key,
        mailbox_name=body.mailbox_name,
        uidvalidity=body.uidvalidity,
        status="accepted",
        total=len(body.emails),
    )
    db.add(job)
    for e in body.emails:
        db.add(SyncStagedEmail(tx_id=tx_id, payload=e.model_dump()))
    db.commit()  # Staging durable machen, bevor 202 rausgeht

    background_tasks.add_task(process_sync_job, tx_id)
    return JSONResponse(status_code=202, content={"tx_id": tx_id, "status": "accepted"})


@router.get("/{tx_id}")
def get_sync_job(tx_id: str, db: Session = Depends(get_session)) -> dict:
    job = db.get(SyncJob, tx_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Sync-Job nicht gefunden.")
    return _job_out(job)
