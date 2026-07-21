"""Asynchrone Verarbeitung eines Sync-Jobs aus dem durablen Staging.

Der HTTP-Request (POST /sync-jobs) hat die Mails nur ins Staging geschrieben und
sofort 202 zurückgegeben. Diese Funktion läuft danach entkoppelt (BackgroundTask)
und übernimmt sie idempotent in den Hauptbestand — außerhalb jedes HTTP-Timeouts.
"""

from __future__ import annotations

import base64

from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal, utcnow
from app.models import Email, Mailbox, SyncJob, SyncStagedEmail


def _upsert_mailbox(db, name: str) -> Mailbox:
    mb = db.scalar(select(Mailbox).where(Mailbox.name == name))
    if mb is None:
        mb = Mailbox(name=name)
        db.add(mb)
        db.flush()  # id vergeben
    return mb


def process_sync_job(tx_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(SyncJob, tx_id)
        if job is None or job.status in ("done", "failed"):
            return  # nichts zu tun / bereits erledigt (idempotenter Retry)

        job.status = "running"
        db.commit()

        mailbox = _upsert_mailbox(db, job.mailbox_name)
        staged = (
            db.scalars(
                select(SyncStagedEmail)
                .where(SyncStagedEmail.tx_id == tx_id)
                .order_by(SyncStagedEmail.id)
            )
            .all()
        )

        now = utcnow().isoformat()
        inserted = skipped = 0
        for i, s in enumerate(staged, start=1):
            p = s.payload
            # Idempotenz über (mailbox, uidvalidity, uid) — wie INSERT OR IGNORE.
            exists = db.scalar(
                select(Email.id).where(
                    Email.mailbox_id == mailbox.id,
                    Email.uidvalidity == p["uidvalidity"],
                    Email.uid == p["uid"],
                )
            )
            if exists:
                skipped += 1
            else:
                db.add(
                    Email(
                        mailbox_id=mailbox.id,
                        uid=p["uid"],
                        uidvalidity=p["uidvalidity"],
                        message_id=p.get("message_id"),
                        from_addr=p.get("from_addr"),
                        to_addr=p.get("to_addr"),
                        subject=p.get("subject"),
                        date_header=p.get("date_header"),
                        internaldate=p.get("internaldate"),
                        size=p.get("size"),
                        raw=base64.b64decode(p["raw_base64"]),
                        imported_at=now,
                    )
                )
                inserted += 1

            job.processed = i
            job.inserted = inserted
            job.skipped = skipped
            # Fortschritt periodisch sichtbar machen (Client pollt processed/total).
            if i % settings.JOB_COMMIT_EVERY == 0:
                db.commit()

        job.status = "done"
        # Staging aufräumen — der Upload ist dauerhaft im Hauptbestand angekommen.
        db.execute(delete(SyncStagedEmail).where(SyncStagedEmail.tx_id == tx_id))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — Fehler landet als Job-Status, nicht als Crash
        db.rollback()
        job = db.get(SyncJob, tx_id)
        if job is not None:
            job.status = "failed"
            job.errors = [str(exc)]
            db.commit()
    finally:
        db.close()
