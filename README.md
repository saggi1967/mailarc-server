# mailarc-server

Zentrale REST-Gegenstelle zum **imap-archiver**-Client (`STORAGE_BACKEND=rest`).
Hält die zentral verschlüsselten IMAP-Konten und die gemeinsame Mail-Ablage,
sodass mehrere CLI-Instanzen in **eine** Datenbank sammeln.

> Rekonstruiert aus dem Client-Vertrag (`app/accounts.py`, `app/storage/rest.py`)
> und `VORSCHLAG-zentrale-speicherung.md`. FastAPI + SQLAlchemy, DB-agnostisch
> (SQLite out-of-the-box, Postgres im Betrieb).

## Schnellstart

```bash
cd mailarc-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"

cp .env.example .env
# API_TOKEN und SECRET_KEY setzen:
python -c "from cryptography.fernet import Fernet; print('SECRET_KEY=' + Fernet.generate_key().decode())" >> .env
echo "API_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env

uvicorn app.main:app --reload      # http://localhost:8000  ·  Docs: /docs
```

Im Client (`imap-archiver/.env`) passend dazu:

```env
STORAGE_BACKEND=rest
REST_BASE_URL=http://localhost:8000
REST_API_TOKEN=<derselbe Wert wie API_TOKEN oben>
```

## Endpunkte (Client-Vertrag)

| Bereich | Endpunkte |
|---|---|
| Konten | `GET/POST /accounts`, `PATCH/DELETE /accounts/{name}`, `GET /accounts/{name}/credentials` |
| Ordner | `GET/POST /mailboxes`, `GET /mailboxes/{name}`, `PATCH /mailboxes/{id}`, `POST/DELETE /mailboxes/{name}/sync-lock` |
| Sync | `POST /sync-jobs` (202 + `tx_id`), `GET /sync-jobs/{tx_id}` |
| Mails | `GET /emails`, `GET /emails/count`, `PATCH /emails/mark-indexed`, `GET /emails/{mb}/{uidv}/{uid}/raw`, `GET /emails/by-message-id/{mid}/raw` |
| Statistik | `GET /stats/summary` |

Alle fachlichen Endpunkte verlangen `Authorization: Bearer <API_TOKEN>`.

## Design (nach Vorschlag)

- **Idempotenz = Korrektheit:** Mails eindeutig über `(mailbox, uidvalidity, uid)`;
  Sync-Jobs zusätzlich über `idempotency_key`. Retries duplizieren nicht.
- **Wasserzeichen nur vorwärts:** `last_uid` per `MAX(...)`.
- **Async Sync-Jobs + durables Staging:** Upload landet zuerst in `sync_staged_email`,
  dann `202`, dann entkoppelte Verarbeitung (`app/jobs.py`) außerhalb jedes HTTP-Timeouts.
- **Per-Ordner-Advisory-Lock** als Lease mit TTL (läuft bei Absturz automatisch ab).
- **Passwörter** liegen nur als Fernet-Token in der DB.

## Test

```bash
pytest -q          # End-to-End-Smoke-Test über den kompletten Vertrag
```

## Postgres

```env
DATABASE_URL=postgresql+psycopg://mailarc:geheim@localhost:5432/mailarc
```
Dazu `pip install -e ".[postgres]"`. Schema wird beim Start automatisch angelegt
(`Base.metadata.create_all`); für echte Migrationen ggf. Alembic ergänzen.
