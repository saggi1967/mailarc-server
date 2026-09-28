"""Asynchrone Verarbeitung eines Sync-Jobs aus dem durablen Staging.

Der HTTP-Request (POST /sync-jobs) hat die Mails nur ins Staging geschrieben und
sofort 202 zurückgegeben. Diese Funktion läuft danach entkoppelt (BackgroundTask)
und übernimmt sie idempotent in den Hauptbestand — außerhalb jedes HTTP-Timeouts.
"""

from __future__ import annotations

import base64

from sqlalchemy import delete, func, select, update

from app.config import settings
from app.db import SessionLocal, utcnow
from app.models import Email, IndexJob, Mailbox, SyncJob, SyncStagedEmail


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


def _pending_email_query(reindex: bool):
    """Basisabfrage der zu indexierenden Mails (join Mailbox für den Ordnernamen)."""
    q = select(Email, Mailbox.name).join(Mailbox, Email.mailbox_id == Mailbox.id)
    if not reindex:
        q = q.where(Email.es_indexed_at.is_(None))
    return q


def process_index_job(tx_id: str) -> None:
    """Baut die ES-Dokumente aus den Roh-Mails und schreibt sie nach Elasticsearch.

    Läuft entkoppelt (BackgroundTask), damit der HTTP-Request sofort 202 geben kann.
    Verarbeitet die Mails seitenweise per id-Cursor — so bleibt der Speicher trotz
    der Roh-Bytes beschränkt. Erfolgreich indexierte Mails bekommen ``es_indexed_at``.
    """
    from elasticsearch.helpers import streaming_bulk

    from app import es, indexdoc

    db = SessionLocal()
    try:
        job = db.get(IndexJob, tx_id)
        if job is None or job.status in ("done", "failed"):
            return  # nichts zu tun / bereits erledigt (idempotenter Retry)

        job.status = "running"
        db.commit()
        reindex = job.reindex

        # Index/Mapping sicherstellen — der Client ruft kein ES mehr direkt.
        esc = es.client()
        es.ensure_index(esc, settings.ES_INDEX)
        es.sync_mapping(esc, settings.ES_INDEX)

        total = db.scalar(
            _pending_email_query(reindex).with_only_columns(func.count(Email.id))
        ) or 0
        job.total = total
        db.commit()

        now = utcnow().isoformat()
        processed = indexed = failed = 0
        errors: list[str] = []
        cursor = 0
        while True:
            page = db.execute(
                _pending_email_query(reindex)
                .where(Email.id > cursor)
                .order_by(Email.id)
                .limit(settings.INDEX_BULK_SIZE)
            ).all()
            if not page:
                break

            id_map: dict[str, int] = {}
            actions: list[dict] = []
            for email, mailbox_name in page:
                cursor = max(cursor, email.id)
                doc = indexdoc.build_document(
                    email.raw,
                    mailbox_name,
                    email.uid,
                    email.uidvalidity,
                    email.internaldate,
                    email.size,
                )
                _id = indexdoc.doc_id(mailbox_name, email.uidvalidity, email.uid)
                id_map[_id] = email.id
                actions.append({"_index": settings.ES_INDEX, "_id": _id, "_source": doc})

            done_ids: list[int] = []
            for ok, info in streaming_bulk(
                esc, actions, chunk_size=settings.INDEX_BULK_SIZE, raise_on_error=False
            ):
                processed += 1
                es_id = info.get("index", {}).get("_id")
                if ok:
                    indexed += 1
                    if es_id in id_map:
                        done_ids.append(id_map[es_id])
                else:
                    failed += 1
                    if len(errors) < 50:  # Fehlerliste begrenzen
                        errors.append(f"{es_id}: {info}")

            if done_ids:
                db.execute(
                    update(Email).where(Email.id.in_(done_ids)).values(es_indexed_at=now)
                )
            job.processed = processed
            job.indexed = indexed
            job.failed = failed
            job.errors = errors
            db.commit()

        esc.indices.refresh(index=settings.ES_INDEX)
        job.status = "done"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — Fehler landet als Job-Status, nicht als Crash
        db.rollback()
        job = db.get(IndexJob, tx_id)
        if job is not None:
            job.status = "failed"
            job.errors = (job.errors or []) + [str(exc)]
            db.commit()
    finally:
        db.close()
