<div align="center">

# 🗄️ mailarc-server

**Zentrale REST-Gegenstelle für den [imap-archiver](https://github.com/saggi1967/imap-archiver) — verschlüsselte IMAP-Konten und gemeinsame Mail-Ablage.**

[![Version](https://img.shields.io/badge/version-2.4.0.0-blue)](#)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](#)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)](#)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00)](#)
[![Postgres](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](#)
[![License](https://img.shields.io/badge/license-proprietär-lightgrey)](#lizenz)

</div>

---

`mailarc-server` ist der zentrale Dienst, gegen den mehrere `imap-archiver`-CLIs
mit `STORAGE_BACKEND=rest` arbeiten. Er hält die **verschlüsselten IMAP-Zugänge**
und die **gemeinsame Mail-Datenbank**, sodass viele Instanzen in **eine** zentrale
Ablage sammeln statt jeweils in lokale SQLite-Dateien.

```
┌──────────────┐   read-only    ┌──────────────────┐   HTTP / Bearer   ┌──────────────────┐
│ IMAP-Server  │ ─────────────▶ │ imap-archiver CLI│ ────────────────▶ │  mailarc-server  │
│  (EXAMINE)   │                │  (RestStorage)   │ ◀──────────────── │  FastAPI + DB    │
└──────────────┘                └──────────────────┘   Sync-Jobs/Poll  └────────┬─────────┘
                                                                                 │
                                                          zentrale DB (Postgres) ▼
```

> Rekonstruiert aus dem Client-Vertrag (`app/accounts.py`, `app/storage/rest.py`)
> und `VORSCHLAG-zentrale-speicherung.md` des imap-archivers.

## Inhalt

- [Funktionsumfang](#funktionsumfang)
- [Schnellstart](#schnellstart)
- [Docker](#docker)
- [Konfiguration](#konfiguration)
- [API-Endpunkte](#api-endpunkte)
- [client-API & Web-Frontend](#client-api--web-frontend)
- [Designprinzipien](#designprinzipien)
- [Datenmodell](#datenmodell)
- [Tests](#tests)
- [Client anbinden](#client-anbinden)
- [Troubleshooting](#troubleshooting)
- [Projektstruktur](#projektstruktur)
- [Lizenz](#lizenz)

## Funktionsumfang

| | Funktion |
|---|---|
| 🔐 | **Verschlüsselte Konten** – IMAP-Passwörter liegen nur als Fernet-Token in der DB |
| 🔑 | **Bearer-Auth** – jeder fachliche Endpunkt erfordert ein gültiges `API_TOKEN` |
| 📥 | **Async Sync-Jobs** – Upload landet durable im Staging, `202` + Poll, Verarbeitung entkoppelt |
| ♻️ | **Idempotenz** – `(mailbox, uidvalidity, uid)` + `idempotency_key` → keine Duplikate bei Retries |
| ⏩ | **Watermark nur vorwärts** – `last_uid` per `MAX(...)`, sicher bei parallelem Sync |
| 🔒 | **Per-Ordner-Locks** – Advisory-Lease mit TTL, läuft bei Absturz automatisch ab |
| 📊 | **Serverseitige Statistik** – Aggregation in der DB statt alle Zeilen zum Client zu ziehen |
| 🐘 | **DB-agnostisch** – SQLite out-of-the-box, Postgres im Betrieb (reiner `DATABASE_URL`-Wechsel) |
| 🖥️ | **client-API (`/api`)** – Suche, Mail-Detail, PDF, Anhänge, Statistik für Web-UI/Rich-Client; **Session-Cookie-Auth** getrennt vom CLI-Bearer-Token |

## Schnellstart

> Voraussetzung: **Python ≥ 3.11**.

```bash
git clone https://github.com/saggi1967/mailarc-server.git
cd mailarc-server

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"

cp .env.example .env
# Pflicht-Secrets erzeugen:
python -c "from cryptography.fernet import Fernet; print('SECRET_KEY=' + Fernet.generate_key().decode())" >> .env
echo "API_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env

uvicorn app.main:app --reload      # http://localhost:8000  ·  Swagger-Docs: /docs
```

Der Server legt sein Schema beim Start automatisch an (`Base.metadata.create_all`).

## Docker

Kompletter Stack (Server + Postgres) über `docker-compose.yml`. Zuerst eine `.env`
**neben** der Compose-Datei mit den Secrets für die Variablen-Substitution anlegen:

```bash
{
  echo "API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  echo "SECRET_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  echo "POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
} > .env

docker compose up -d --build       # Server auf http://localhost:9000 (Container-Port 8000)
```

Der Server startet erst, wenn Postgres `healthy` ist (`depends_on: condition: service_healthy`).
Fehlt `API_TOKEN` oder `SECRET_KEY`, bricht Compose bewusst mit klarer Meldung ab.

> Der `server`-Service bindet die `.env` per **`env_file`** ein — dadurch gelangen auch
> **`WEB_*`** (Web-Login) und **`ES_*`** (Suche) in den Container. Für den Web-Login also
> `WEB_PASSWORD` in dieselbe `.env` schreiben; `ES_HOST` im Container auf
> `host.docker.internal` (Host-ES) bzw. den ES-Service-Namen zeigen lassen, **nicht**
> `localhost`.

Nur das Server-Image (mit eigener DB):

```bash
docker build -t mailarc-server .
docker run -p 8000:8000 -v mailarc-data:/data \
  -e DATABASE_URL=sqlite:////data/mailarc.db \
  -e API_TOKEN=... -e SECRET_KEY=... mailarc-server
```

## Konfiguration

Alle Einstellungen kommen aus Umgebungsvariablen bzw. einer `.env` (via `pydantic-settings`).
Vorlage: [`.env.example`](.env.example).

| Variable | Default | Bedeutung |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./mailarc_server.db` | Ziel-DB; Postgres: `postgresql+psycopg://user:pass@host:5432/db` |
| `API_TOKEN` | – | **Pflicht.** Muss dem `REST_API_TOKEN` der Clients entsprechen (Bearer-Auth) |
| `SECRET_KEY` | – | **Pflicht.** Fernet-Schlüssel für die Passwort-Verschlüsselung |
| `LOCK_TTL_SECONDS` | `300` | Lease-Dauer der Per-Ordner-Sync-Locks |
| `MAX_PAGE_LIMIT` | `1000` | Obergrenze der Seitengröße für `GET /emails` |
| `JOB_COMMIT_EVERY` | `200` | Fortschritt eines Sync-Jobs alle N Mails persistieren |
| `WEB_USERNAME` | `admin` | Benutzername für den Web-Login (client-API) |
| `WEB_PASSWORD` | – | **Für Web-Login Pflicht.** Leer = Login deaktiviert („Login gesperrt") |
| `WEB_ORIGINS` | `http://localhost:5173` | Erlaubte Frontend-Origins (CORS, Komma-getrennt) |
| `WEB_SESSION_TTL` | `43200` | Gültigkeit des Session-Cookies in Sekunden (12 h) |
| `WEB_COOKIE_SECURE` / `WEB_COOKIE_SAMESITE` | `false` / `lax` | Cookie-Flags; hinter HTTPS `true`, bei fremder Domain `none` |
| `ES_HOST` / `ES_USER` / `ES_PASSWORD` | `http://localhost:9200` / `elastic` / – | Elasticsearch für `/api/search` (gleiche Instanz/Index wie der CLI-Indexlauf) |
| `ES_INDEX` / `ES_VERIFY_CERTS` | `emails` / `true` | Ziel-Index; TLS-Prüfung (nur bei https) |

> 🖥️ **Web-Login aktivieren:** `WEB_PASSWORD` setzen (und `ES_*` für die Suche), dann Server neu starten. Ohne `WEB_PASSWORD` meldet das Frontend „Login gesperrt". Im Docker-Container ist `localhost` der Container selbst — für ES auf dem Host `host.docker.internal` verwenden.

`SECRET_KEY` erzeugen:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> ⚠️ Ändert sich der `SECRET_KEY`, sind bereits gespeicherte Passwörter nicht mehr
> entschlüsselbar — Konten dann per `mailarc account update` neu setzen.

## API-Endpunkte

Zwei getrennte Verträge: der **interne CLI-Vertrag** (Bearer-Token, für den
`imap-archiver`-Client) und die **client-API `/api`** (Session-Cookie, für Web-UI
und Rich-Clients — siehe [nächster Abschnitt](#client-api--web-frontend)). Die
CLI-Endpunkte erfordern `Authorization: Bearer <API_TOKEN>`. Fehler kommen als
`{"detail": "..."}`; interaktive Doku unter `/docs`.

| Bereich | Endpunkte |
|---|---|
| **Meta** | `GET /health` (ohne Auth) |
| **Konten** | `GET/POST /accounts` · `PATCH/DELETE /accounts/{name}` · `GET /accounts/{name}/credentials` |
| **Ordner** | `GET/POST /mailboxes` · `GET /mailboxes/{name}` · `PATCH /mailboxes/{id}` · `POST/DELETE /mailboxes/{name}/sync-lock` |
| **Sync** | `POST /sync-jobs` → `202 {tx_id}` · `GET /sync-jobs/{tx_id}` |
| **Mails** | `GET /emails` · `GET /emails/count` · `PATCH /emails/mark-indexed` · `GET /emails/{mb}/{uidv}/{uid}/raw` · `GET /emails/by-message-id/{mid}/raw` |
| **Statistik** | `GET /stats/summary` |

`GET /accounts/{name}/credentials` ist der einzige Endpunkt, der das Passwort
**entschlüsselt** ausliefert — der Client lädt es kurz vor dem read-only IMAP-Zugriff.

## client-API & Web-Frontend

Unter dem Präfix **`/api`** liegt eine **client-neutrale** API für ein Web-Frontend
([`mailarc-web`](https://github.com/saggi1967/mailarc-web), React + Material UI) und
künftige Rich-Clients. Statt des statischen Bearer-Tokens nutzt sie ein **signiertes
httpOnly-Session-Cookie** (HMAC über `SECRET_KEY`) — kein Token im Browser-JavaScript.

| Bereich | Endpunkte |
|---|---|
| **Auth** | `POST /api/auth/login` · `GET /api/auth/me` · `POST /api/auth/logout` |
| **Suche** | `GET /api/search` (Filter, Pagination, Highlight) · `GET /api/search/count` · `GET /api/search/top` |
| **Einzelmail** | `GET /api/emails/{id}` (Detail) · `GET /api/emails/{id}/pdf` · `GET /api/emails/{id}/attachments` (+ `/{n}`) |
| **Statistik** | `GET /api/stats/summary` |

Voraussetzungen für den Betrieb:

1. **`WEB_PASSWORD`** setzen (sonst ist der Login deaktiviert), optional `WEB_USERNAME`.
2. **`ES_*`** auf die Elasticsearch-Instanz mit dem indexierten `emails`-Index zeigen
   lassen (die `/api/search`-Logik entspricht 1:1 der CLI-Suche).
3. **`WEB_ORIGINS`** auf die Frontend-URL setzen (Default deckt den Vite-Dev-Server
   `http://localhost:5173` ab). CORS läuft mit `credentials`, das Cookie wird gesetzt.

```bash
# .env des Servers (Auszug)
WEB_USERNAME=admin
WEB_PASSWORD=<geheim>
WEB_ORIGINS=http://localhost:5173
ES_HOST=http://localhost:9200   # im Container: host.docker.internal
ES_USER=elastic
ES_PASSWORD=<geheim>
ES_INDEX=emails
```

Das **PDF-Rendering** (`/api/emails/{id}/pdf`) braucht WeasyPrint samt nativer Libs;
optional installieren mit `pip install -e ".[pdf]"` (fehlt es, antwortet der Endpunkt
sauber mit `503`). Das Frontend liegt im eigenen Repo `mailarc-web` und wird über
`VITE_API_BASE` auf diesen Server gezeigt.

## Designprinzipien

Aus dem Konzeptpapier `VORSCHLAG-zentrale-speicherung.md` umgesetzt:

- **Idempotenz = Korrektheit.** Mails sind über `(mailbox, uidvalidity, uid)` eindeutig,
  Sync-Jobs zusätzlich über `idempotency_key`. Ein wiederholter Upload dupliziert nichts.
- **Wasserzeichen nur vorwärts.** `last_uid` wird per `MAX(...)` gesetzt — paralleler
  Sync desselben Ordners bleibt datensicher, egal wer zuletzt schreibt.
- **Async Sync-Jobs + durables Staging.** Der Upload landet erst in `sync_staged_email`,
  dann kommt `202`, dann läuft die Übernahme in den Hauptbestand entkoppelt
  (`app/jobs.py`) — außerhalb jedes HTTP-Timeouts.
- **Per-Ordner-Advisory-Lock** als Lease mit TTL: Effizienz-Schicht, die doppelte
  IMAP-Arbeit vermeidet, ohne dass ein Absturz den Ordner dauerhaft sperrt.
- **Passwörter** liegen ausschließlich als Fernet-Token in der DB.

## Datenmodell

| Tabelle | Zweck |
|---|---|
| `account` | Zentrale IMAP-Zugänge; Passwort als Fernet-Token (`imap_password_enc`) |
| `mailbox` | Sync-Stand je Ordner (`uidvalidity`, `last_uid`, `last_import_at`) |
| `email` | Roh-RFC822 (`raw`) + geparste Felder; UNIQUE `(mailbox_id, uidvalidity, uid)` |
| `sync_job` | Status eines Uploads (`accepted`/`running`/`done`/`failed`, Zähler, Fehler) |
| `sync_staged_email` | Durables Staging der hochgeladenen Mails bis zur Verarbeitung |
| `sync_lock` | Per-Ordner-Lease mit `expires_at` |

Das `mailbox`/`email`-Schema spiegelt exakt die lokale SQLite-Ablage des Clients.

## Tests

```bash
pytest -q
```

Der End-to-End-Smoke-Test (`tests/test_smoke.py`) fährt den kompletten Client-Vertrag
über den `TestClient`: Auth, Konten inkl. Passwort-Update, Mailbox-Upsert mit
Forward-only-Watermark, Sync-Job (`202` → Poll → `done`) inklusive Idempotenz-Retry,
Emails `count`/`list`/`raw`/`mark-indexed`, Lock `200`/`409`/Release und `stats/summary`.

## Client anbinden

In der `.env` des [imap-archiver](https://github.com/saggi1967/imap-archiver):

```bash
STORAGE_BACKEND=rest
REST_BASE_URL=http://localhost:9000     # bzw. https://archiv.firma.example
REST_API_TOKEN=<identisch zu API_TOKEN hier>
```

Optional zentrale, verschlüsselte IMAP-Zugänge statt Passwort je `.env`:

```bash
mailarc account add                     # legt Konto zentral an (Passwort verschlüsselt)
mailarc account update <label>          # Konto ändern / Passwort neu setzen
# in der Client-.env dann nur noch:  ACCOUNT=<label>
```

## Troubleshooting

### `FATAL: password authentication failed for user "mailarc"`

Kein Code-Fehler, sondern ein Postgres-Volume-Effekt: Postgres übernimmt
`POSTGRES_PASSWORD` **nur bei der Erst-Initialisierung** eines leeren
Datenverzeichnisses. Existiert das Volume `pgdata` schon aus einem früheren
`docker compose up`, behält die DB ihr damaliges Passwort — eine später
geänderte `.env` erreicht sie nicht mehr.

**Fix A — Volume neu aufsetzen** (wenn keine wichtigen Daten drin sind):

```bash
docker compose down -v && docker compose up -d --build
```

**Fix B — Passwort in-place angleichen** (erhält vorhandene Daten):

```bash
PW=$(docker compose config | grep -oE 'mailarc:[^@]+@db' | head -1 | sed -E 's#mailarc:(.+)@db#\1#')
docker compose exec -T db psql -U mailarc -d mailarc -c "ALTER USER mailarc PASSWORD '$PW';"
docker compose restart server
```

> Nach dem ersten Start die `.env`-Passwörter nicht mehr ändern — sonst einen der
> beiden Wege oben gehen. Im Betrieb ein langes Zufallspasswort verwenden.

## Projektstruktur

```
mailarc-server/
├── app/
│   ├── main.py            # FastAPI-App, Lifespan-Schema-Setup, Bearer-Auth
│   ├── config.py          # Einstellungen (.env)
│   ├── db.py              # Engine/Session, naive-UTC, SQLite↔Postgres
│   ├── models.py          # ORM: account, mailbox, email, sync_job, staging, lock
│   ├── schemas.py         # Pydantic-Modelle (Client-Vertrag)
│   ├── security.py        # Bearer-Auth + Fernet-Verschlüsselung
│   ├── webauth.py         # client-API: Session-Cookie-Auth (HMAC)
│   ├── es.py              # client-API: Elasticsearch-Client + build_query
│   ├── render.py          # client-API: Mail → PDF (WeasyPrint, optional)
│   ├── mailparse.py       # client-API: Anhänge/Decode aus der Roh-Mail
│   ├── jobs.py            # async Sync-Job-Verarbeitung aus dem Staging
│   └── routers/           # accounts · mailboxes · emails · sync_jobs · stats · client (/api)
├── tests/                 # test_smoke.py (CLI-Vertrag) · test_client_api.py (/api)
├── Dockerfile
├── docker-compose.yml     # Server + Postgres
└── pyproject.toml
```

## Roadmap

- **Alembic-Migrationen** statt `create_all` für versionierte Schema-Änderungen.
- **Serverseitiger Index-Lauf**, damit `index run` nicht jede Roh-Mail über HTTP zieht
  (größter Einzelposten laut Konzeptpapier, Abschnitt 7.1).

## Lizenz

Proprietär / intern – © Microtronix. Keine Weitergabe ohne Freigabe.

---

<div align="center">
<sub>Gebaut mit Python · FastAPI · SQLAlchemy · Pydantic · Cryptography</sub>
</div>
