"""End-to-End-Smoke-Test des kompletten Client-Vertrags über den TestClient.

Setzt Konfiguration vor dem App-Import (Settings werden beim Import gelesen) und
fährt alle Endpunkte in der Reihenfolge durch, in der der Client sie benutzt.
"""

import base64
import importlib
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./test_mailarc_server.db"
os.environ["API_TOKEN"] = "test-token"
from cryptography.fernet import Fernet  # noqa: E402

os.environ["SECRET_KEY"] = Fernet.generate_key().decode()

# Frische DB-Datei je Lauf.
if os.path.exists("./test_mailarc_server.db"):
    os.remove("./test_mailarc_server.db")

# App (und Settings) frisch importieren.
for mod in [m for m in list(sys.modules) if m.startswith("app")]:
    del sys.modules[mod]

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

AUTH = {"Authorization": "Bearer test-token"}


def test_full_flow():
    with TestClient(app) as c:
        # -- Auth --------------------------------------------------------
        assert c.get("/accounts").status_code == 401
        assert c.get("/health").json() == {"status": "ok"}

        # -- Accounts: add / list / patch / credentials ------------------
        acc = {
            "name": "firma",
            "imap_host": "mail.firma.example",
            "imap_port": 993,
            "imap_ssl": True,
            "imap_ssl_verify": True,
            "imap_user": "u@firma.example",
            "imap_password": "geheim1",
            "folders": "INBOX,Sent",
        }
        assert c.post("/accounts", json=acc, headers=AUTH).status_code == 201
        rows = c.get("/accounts", headers=AUTH).json()
        assert rows[0]["name"] == "firma" and "imap_password" not in rows[0]

        # Passwort ändern (das ist der 'account update'-Pfad des Clients).
        r = c.patch("/accounts/firma", json={"imap_password": "geheim2"}, headers=AUTH)
        assert r.status_code == 200
        creds = c.get("/accounts/firma/credentials", headers=AUTH).json()
        assert creds["imap_password"] == "geheim2"
        assert creds["imap_host"] == "mail.firma.example"

        # -- Mailbox upsert + state --------------------------------------
        mb_id = c.post("/mailboxes", json={"name": "INBOX"}, headers=AUTH).json()["id"]
        assert c.patch(
            f"/mailboxes/{mb_id}",
            json={"uidvalidity": 42, "last_uid": 10, "last_import_at": "2026-07-21T10:00:00"},
            headers=AUTH,
        ).status_code == 200
        # Wasserzeichen nur vorwärts: kleinerer last_uid darf nicht zurücksetzen.
        c.patch(f"/mailboxes/{mb_id}", json={"last_uid": 5}, headers=AUTH)
        assert c.get("/mailboxes/INBOX", headers=AUTH).json()["last_uid"] == 10

        # -- Sync-Job: upload + poll (idempotent) ------------------------
        raw = b"From: a@x\r\nSubject: Hallo\r\n\r\nBody"
        email = {
            "uid": 1,
            "uidvalidity": 42,
            "message_id": "<m1@x>",
            "from_addr": "Alice <a@x>",
            "to_addr": "b@y",
            "subject": "Hallo",
            "date_header": "2026-07-20T09:00:00",
            "internaldate": "2026-07-20T09:00:01",
            "size": len(raw),
            "raw_base64": base64.b64encode(raw).decode(),
        }
        job = {
            "idempotency_key": "INBOX:42:1-1:1",
            "mailbox_name": "INBOX",
            "uidvalidity": 42,
            "last_uid": None,
            "emails": [email],
        }
        r = c.post("/sync-jobs", json=job, headers=AUTH)
        assert r.status_code == 202
        tx_id = r.json()["tx_id"]
        # TestClient führt BackgroundTasks synchron aus → Job ist bereits fertig.
        status = c.get(f"/sync-jobs/{tx_id}", headers=AUTH).json()
        assert status["status"] == "done"
        assert status["inserted"] == 1 and status["skipped"] == 0

        # Idempotenter Retry: gleicher Key liefert denselben Job, kein Duplikat.
        r2 = c.post("/sync-jobs", json=job, headers=AUTH)
        assert r2.json()["tx_id"] == tx_id

        # -- Emails: count / list / raw / mark-indexed -------------------
        assert c.get("/emails/count?reindex=false", headers=AUTH).json()["count"] == 1
        page = c.get("/emails?index_pending=1&reindex=false&cursor=0&limit=200", headers=AUTH).json()
        assert len(page["items"]) == 1
        eid = page["items"][0]["id"]
        assert base64.b64decode(page["items"][0]["raw_base64"]) == raw

        got = c.get("/emails/INBOX/42/1/raw", headers=AUTH)
        assert got.content == raw
        assert c.get("/emails/by-message-id/<m1@x>/raw", headers=AUTH).content == raw

        c.patch("/emails/mark-indexed", json={"ids": [eid], "indexed_at": "2026-07-21T11:00:00"}, headers=AUTH)
        assert c.get("/emails/count?reindex=false", headers=AUTH).json()["count"] == 0

        # -- Locks -------------------------------------------------------
        lock = c.post("/mailboxes/INBOX/sync-lock", headers=AUTH)
        assert lock.status_code == 200 and "lease_id" in lock.json()
        assert c.post("/mailboxes/INBOX/sync-lock", headers=AUTH).status_code == 409
        assert c.delete("/mailboxes/INBOX/sync-lock", headers=AUTH).status_code == 200

        # -- Stats -------------------------------------------------------
        s = c.get("/stats/summary?top=5", headers=AUTH).json()
        assert s["total"] == 1 and s["total_size"] == len(raw)
        assert s["distinct_senders"] == 1
        assert s["per_year"]["2026"] == 1

        # -- Account remove ---------------------------------------------
        assert c.delete("/accounts/firma", headers=AUTH).status_code == 200
        assert c.get("/accounts", headers=AUTH).json() == []
