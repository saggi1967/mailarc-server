"""Tests der client-API (/api): Session-Auth real, Suche mit gefaktem ES-Client.

Wie test_smoke.py wird die Konfiguration vor dem App-Import gesetzt (Settings
werden beim Import gelesen).
"""

import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./test_client_api.db"
os.environ["API_TOKEN"] = "test-token"
os.environ["WEB_USERNAME"] = "admin"
os.environ["WEB_PASSWORD"] = "s3cret"
os.environ["ES_INDEX"] = "emails"

from cryptography.fernet import Fernet  # noqa: E402

os.environ["SECRET_KEY"] = Fernet.generate_key().decode()

if os.path.exists("./test_client_api.db"):
    os.remove("./test_client_api.db")

for mod in [m for m in list(sys.modules) if m.startswith("app")]:
    del sys.modules[mod]

from fastapi.testclient import TestClient  # noqa: E402

from app import es  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Email, Mailbox  # noqa: E402


class FakeES:
    """Minimaler ES-Ersatz: liefert je nach Aufruf feste Antworten."""

    _DOC = {
        "mailbox": "INBOX", "uid": 42, "uidvalidity": 7, "message_id": "<m1@x>",
        "subject": "Rechnung 26/130", "from_addr": "billing@apple.com",
        "from_name": "Apple", "to": ["u@firma.de"], "date": "2026-07-01T09:00:00",
        "has_attachment": True, "attachment_count": 1, "body": "Sehr geehrte …",
    }

    def search(self, **kwargs):
        if kwargs.get("size") == 0:  # Aggregation (top)
            return {"aggregations": {"grp": {"buckets": [{"key": "apple.com", "doc_count": 3}]}}}
        if kwargs.get("query", {}).get("term", {}).get("message_id"):  # detail per message-id
            return {"hits": {"hits": [{"_id": "INBOX:7:42", "_source": self._DOC}]}}
        return {"hits": {"total": {"value": 1}, "hits": [
            {"_id": "INBOX:7:42", "_source": self._DOC,
             "highlight": {"body": ["… <em>Rechnung</em> …"]}}]}}

    def count(self, **kwargs):
        return {"count": 1}

    def exists(self, **kwargs):
        return kwargs.get("id") == "INBOX:7:42"

    def get(self, **kwargs):
        return {"_source": self._DOC}


def test_auth_and_search(monkeypatch):
    monkeypatch.setattr(es, "client", lambda: FakeES())
    with TestClient(app) as c:
        # -- ohne Login gesperrt -----------------------------------------
        assert c.get("/api/auth/me").status_code == 401
        assert c.get("/api/search?q=Rechnung").status_code == 401

        # -- Login falsch / richtig --------------------------------------
        assert c.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
        r = c.post("/api/auth/login", json={"username": "admin", "password": "s3cret"})
        assert r.status_code == 200 and r.json()["user"] == "admin"
        assert "mailarc_session" in r.cookies  # httpOnly-Cookie gesetzt

        # -- Session gilt (TestClient hält das Cookie) -------------------
        assert c.get("/api/auth/me").json() == {"user": "admin", "role": "admin"}

        # -- Suche -------------------------------------------------------
        res = c.get("/api/search?q=Rechnung&from=billing@apple.com&limit=10").json()
        assert res["total"] == 1 and res["count"] == 1
        item = res["items"][0]
        assert item["id"] == "INBOX:7:42" and item["subject"] == "Rechnung 26/130"
        assert "Rechnung" in item["snippet"]

        assert c.get("/api/search/count?q=Rechnung").json() == {"count": 1}
        top = c.get("/api/search/top?by=from_domain").json()
        assert top["buckets"][0] == {"key": "apple.com", "count": 3}
        # unerlaubtes Aggregat-Feld abgewiesen
        assert c.get("/api/search/top?by=body").status_code == 422

        # -- Einzelmail (per ref und per message-id) ---------------------
        d = c.get("/api/emails/INBOX:7:42").json()
        assert d["subject"] == "Rechnung 26/130" and d["body"].startswith("Sehr geehrte")
        assert c.get("/api/emails/<m1@x>").json()["from_addr"] == "billing@apple.com"

        # -- Logout entzieht den Zugang ----------------------------------
        assert c.post("/api/auth/logout").json() == {"ok": True}
        assert c.get("/api/auth/me").status_code == 401


def test_user_management():
    with TestClient(app) as c:
        assert c.get("/api/users").status_code == 401  # ohne Session
        c.post("/api/auth/login", json={"username": "admin", "password": "s3cret"})

        # Geseedeter Admin ist da.
        users = c.get("/api/users").json()
        assert {"username": "admin", "role": "admin", "is_active": True} in users

        # Anlegen (normaler Benutzer)
        assert c.post("/api/users", json={"username": "bob", "password": "pw", "role": "user"}).status_code == 201
        assert c.post("/api/users", json={"username": "bob", "password": "x"}).status_code == 409  # Duplikat

        # bob darf sich einloggen, aber KEINE Benutzer verwalten (403)
        with TestClient(app) as cb:
            cb.post("/api/auth/login", json={"username": "bob", "password": "pw"})
            assert cb.get("/api/auth/me").json() == {"user": "bob", "role": "user"}
            assert cb.get("/api/users").status_code == 403
            assert cb.post("/api/users", json={"username": "x", "password": "y"}).status_code == 403

        # Ändern: Passwort + zur Admin-Rolle
        assert c.patch("/api/users/bob", json={"role": "admin", "password": "pw2"}).status_code == 200
        with TestClient(app) as cb:
            assert cb.post("/api/auth/login", json={"username": "bob", "password": "pw2"}).status_code == 200

        # Absicherungen: sich selbst nicht löschen; letzten Admin nicht entmachten
        assert c.delete("/api/users/admin").status_code == 400  # admin ist eingeloggt = self
        # bob (jetzt Admin) löschen ist ok, da admin bleibt
        assert c.delete("/api/users/bob").json() == {"deleted": "bob"}
        # jetzt ist admin der letzte aktive Admin → nicht deaktivierbar/herabstufbar
        assert c.patch("/api/users/admin", json={"is_active": False}).status_code == 400
        assert c.patch("/api/users/admin", json={"role": "user"}).status_code == 400


def test_api_accounts_crud():
    with TestClient(app) as c:
        assert c.get("/api/accounts").status_code == 401  # ohne Session gesperrt
        c.post("/api/auth/login", json={"username": "admin", "password": "s3cret"})

        acc = {
            "name": "webk", "imap_host": "mail.x", "imap_user": "u", "imap_password": "p",
            "folders": "INBOX", "es_host": "http://es:9200", "es_user": "elastic",
            "es_password": "espw",
        }
        assert c.post("/api/accounts", json=acc).status_code == 201
        row = next(x for x in c.get("/api/accounts").json() if x["name"] == "webk")
        assert row["es_host"] == "http://es:9200"
        assert row["es_password_set"] is True and "es_password" not in row

        assert c.patch("/api/accounts/webk", json={"imap_user": "u2"}).status_code == 200
        assert next(x for x in c.get("/api/accounts").json() if x["name"] == "webk")["imap_user"] == "u2"

        assert c.delete("/api/accounts/webk").json() == {"deleted": "webk"}
        assert not any(x["name"] == "webk" for x in c.get("/api/accounts").json())


def test_search_es_unreachable(monkeypatch):
    from elastic_transport import ConnectionError as ESConnectionError

    class DeadES:
        def search(self, **_kw):
            raise ESConnectionError("connection refused")

    monkeypatch.setattr(es, "client", lambda: DeadES())
    with TestClient(app) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": "s3cret"})
        r = c.get("/api/search?q=x")
        assert r.status_code == 503
        assert "Elasticsearch" in r.json()["detail"]


def _insert_sample_email() -> bytes:
    """Legt Mailbox + eine Mail (HTML-Body + ein Anhang) direkt in der DB an."""
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"] = "Apple <billing@apple.com>"
    m["To"] = "u@firma.de"
    m["Subject"] = "Rechnung 26/130"
    m["Date"] = "Wed, 01 Jul 2026 09:00:00 +0000"
    m["Message-ID"] = "<m1@x>"
    m.set_content("Nur Text")
    m.add_alternative("<h1>Rechnung</h1><p>Betrag: 42 EUR</p>", subtype="html")
    m.add_attachment(b"BELEGDATEN", maintype="application", subtype="pdf", filename="beleg.pdf")
    raw = m.as_bytes()

    db = SessionLocal()
    mb = Mailbox(name="INBOX", uidvalidity=7, last_uid=42)
    db.add(mb)
    db.flush()
    db.add(Email(
        mailbox_id=mb.id, uid=42, uidvalidity=7, message_id="<m1@x>",
        from_addr="billing@apple.com", subject="Rechnung 26/130",
        date_header="2026-07-01T09:00:00", internaldate="2026-07-01T09:00:01",
        size=len(raw), raw=raw, imported_at="2026-07-01T09:00:02",
    ))
    db.commit()
    db.close()
    return raw


def test_pdf_attachments_stats():
    with TestClient(app) as c:
        raw = _insert_sample_email()
        assert c.post("/api/auth/login", json={"username": "admin", "password": "s3cret"}).status_code == 200

        # -- Statistik ---------------------------------------------------
        s = c.get("/api/stats/summary").json()
        assert s["total"] == 1 and s["total_size"] == len(raw)
        assert s["per_year"]["2026"] == 1

        # -- Anhänge: Liste + Download -----------------------------------
        att = c.get("/api/emails/INBOX:7:42/attachments").json()
        assert att["count"] == 1
        assert att["attachments"][0]["filename"] == "beleg.pdf"
        assert att["attachments"][0]["content_type"] == "application/pdf"

        dl = c.get("/api/emails/INBOX:7:42/attachments/1")
        assert dl.status_code == 200 and dl.content == b"BELEGDATEN"
        assert "beleg.pdf" in dl.headers["content-disposition"]
        assert c.get("/api/emails/INBOX:7:42/attachments/2").status_code == 404

        # -- PDF (WeasyPrint verfügbar → echtes %PDF) --------------------
        pdf = c.get("/api/emails/INBOX:7:42/pdf")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content[:4] == b"%PDF"

        # auch per Message-ID auflösbar
        assert c.get("/api/emails/<m1@x>/attachments").json()["count"] == 1
