"""Asynchrone Index-Jobs: Anstoß (202) + Status-Poll.

Gegenstelle zu ``RestStorage.start_index_job``/``poll_index_job`` im Client. Der
POST legt den Job an und gibt sofort ``tx_id`` zurück; die eigentliche Indexierung
nach Elasticsearch läuft in ``jobs.process_index_job`` — dort, wo ES erreichbar ist.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.jobs import process_index_job
from app.models import IndexJob
from app.schemas import IndexJobIn

router = APIRouter(prefix="/index-jobs", tags=["index-jobs"])


def _job_out(job: IndexJob) -> dict:
    return {
        "tx_id": job.tx_id,
        "status": job.status,
        "total": job.total,
        "processed": job.processed,
        "indexed": job.indexed,
        "failed": job.failed,
        "errors": job.errors or [],
    }


@router.post("")
def create_index_job(
    body: IndexJobIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    # Läuft bereits ein Indexlauf, keinen zweiten starten — sonst würden zwei
    # Jobs dieselben Mails doppelt bearbeiten. Den laufenden zurückgeben.
    running = db.scalar(
        select(IndexJob).where(IndexJob.status.in_(("accepted", "running")))
    )
    if running is not None:
        return JSONResponse(
            status_code=202, content={"tx_id": running.tx_id, "status": running.status}
        )

    tx_id = uuid.uuid4().hex
    job = IndexJob(tx_id=tx_id, reindex=body.reindex, status="accepted")
    db.add(job)
    db.commit()

    background_tasks.add_task(process_index_job, tx_id)
    return JSONResponse(status_code=202, content={"tx_id": tx_id, "status": "accepted"})


@router.get("/{tx_id}")
def get_index_job(tx_id: str, db: Session = Depends(get_session)) -> dict:
    job = db.get(IndexJob, tx_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Index-Job nicht gefunden.")
    return _job_out(job)
