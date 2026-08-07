"""Leichtgewichtige Session-Auth für die client-API (Web-UI / Rich-Client).

Ein interner Benutzer (``WEB_USERNAME``/``WEB_PASSWORD``). Nach dem Login setzt der
Server ein **signiertes, httpOnly-Cookie** — kein Token im JavaScript. Signiert wird
mit HMAC-SHA256 über den ``SECRET_KEY`` (derselbe Schlüssel wie für die Fernet-
Verschlüsselung; hier nur als HMAC-Key genutzt). Bewusst ohne Zusatzabhängigkeit.

Für Mehrbenutzer/Rollen später gegen echte Benutzerverwaltung + JWT austauschbar —
die Schnittstelle (``current_user``) bleibt gleich.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi import Cookie, HTTPException, status

from app.config import settings

COOKIE_NAME = "mailarc_session"


def _key() -> bytes:
    if not settings.SECRET_KEY:
        raise HTTPException(status_code=500, detail="SECRET_KEY ist serverseitig nicht gesetzt.")
    return settings.SECRET_KEY.encode()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def issue_token(username: str) -> str:
    """Erzeugt ein signiertes Session-Token mit Ablaufzeitpunkt."""
    payload = {"u": username, "exp": int(time.time()) + settings.WEB_SESSION_TTL}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> str | None:
    """Prüft Signatur + Ablauf und liefert den Benutzernamen oder None."""
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload.get("u")
    except Exception:  # noqa: BLE001 — jede Fehlerform bedeutet: ungültig
        return None


def check_login(username: str, password: str) -> bool:
    """Konstantzeit-Vergleich gegen die konfigurierten Zugangsdaten."""
    if not settings.WEB_PASSWORD:
        raise HTTPException(
            status_code=500, detail="WEB_PASSWORD ist serverseitig nicht gesetzt (Login deaktiviert)."
        )
    ok_user = hmac.compare_digest(username, settings.WEB_USERNAME)
    ok_pass = hmac.compare_digest(password, settings.WEB_PASSWORD)
    return ok_user and ok_pass


def current_user(mailarc_session: str | None = Cookie(default=None)) -> str:
    """FastAPI-Dependency: erzwingt eine gültige Session, liefert den Benutzernamen."""
    user = verify_token(mailarc_session) if mailarc_session else None
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht angemeldet."
        )
    return user
