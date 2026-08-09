"""Benutzerverwaltung der client-API (Web-UI).

Passwörter werden mit PBKDF2-HMAC-SHA256 gehasht (stdlib, keine Zusatzabhängigkeit),
Format ``pbkdf2_sha256$iterations$salt_hex$hash_hex``. Der erste Admin wird beim
Start aus ``WEB_USERNAME``/``WEB_PASSWORD`` geseedet (abwärtskompatibel, kein Lockout).

Absicherungen: man kann sich nicht selbst löschen, und der **letzte aktive Admin**
lässt sich nicht entfernen, deaktivieren oder zum Benutzer herabstufen.
"""

from __future__ import annotations

import hashlib
import hmac
import os

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import WebUser

ROLES = {"admin", "user"}
_ITERATIONS = 200_000


# ── Passwort-Hashing ─────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:  # noqa: BLE001 — jede Fehlerform bedeutet: ungültig
        return False


# ── Abfragen ─────────────────────────────────────────────────────────────────
def get(db: Session, username: str) -> WebUser | None:
    return db.scalar(select(WebUser).where(WebUser.username == username))


def get_active(db: Session, username: str) -> WebUser | None:
    return db.scalar(select(WebUser).where(WebUser.username == username, WebUser.is_active.is_(True)))


def authenticate(db: Session, username: str, password: str) -> WebUser | None:
    u = get_active(db, username)
    if u and verify_password(password, u.password_hash):
        return u
    return None


def _active_admins(db: Session, exclude: str | None = None) -> int:
    q = select(func.count(WebUser.id)).where(WebUser.role == "admin", WebUser.is_active.is_(True))
    if exclude:
        q = q.where(WebUser.username != exclude)
    return db.scalar(q) or 0


def _out(u: WebUser) -> dict:
    return {"username": u.username, "role": u.role, "is_active": u.is_active}


# ── CRUD ─────────────────────────────────────────────────────────────────────
def list_users(db: Session) -> list[dict]:
    return [_out(u) for u in db.scalars(select(WebUser).order_by(WebUser.username)).all()]


def create_user(db: Session, username: str, password: str, role: str) -> dict:
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"Rolle muss aus {sorted(ROLES)} sein.")
    if not username or not password:
        raise HTTPException(status_code=422, detail="Benutzername und Passwort sind erforderlich.")
    if get(db, username):
        raise HTTPException(status_code=409, detail=f"Benutzer '{username}' existiert bereits.")
    u = WebUser(username=username, password_hash=hash_password(password), role=role, is_active=True)
    db.add(u)
    db.flush()
    return _out(u)


def update_user(
    db: Session,
    username: str,
    acting: str,
    password: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> dict:
    u = get(db, username)
    if u is None:
        raise HTTPException(status_code=404, detail=f"Benutzer '{username}' nicht gefunden.")

    # Letzten aktiven Admin nicht entmachten (Herabstufung oder Deaktivierung).
    losing_admin = (role is not None and role != "admin") or (is_active is False)
    if u.role == "admin" and u.is_active and losing_admin and _active_admins(db, exclude=username) == 0:
        raise HTTPException(status_code=400, detail="Der letzte aktive Admin kann nicht entmachtet werden.")

    if role is not None:
        if role not in ROLES:
            raise HTTPException(status_code=422, detail=f"Rolle muss aus {sorted(ROLES)} sein.")
        u.role = role
    if is_active is not None:
        u.is_active = is_active
    if password:
        u.password_hash = hash_password(password)
    db.flush()
    return _out(u)


def delete_user(db: Session, username: str, acting: str) -> dict:
    u = get(db, username)
    if u is None:
        raise HTTPException(status_code=404, detail=f"Benutzer '{username}' nicht gefunden.")
    if username == acting:
        raise HTTPException(status_code=400, detail="Man kann sich nicht selbst löschen.")
    if u.role == "admin" and u.is_active and _active_admins(db, exclude=username) == 0:
        raise HTTPException(status_code=400, detail="Der letzte aktive Admin kann nicht gelöscht werden.")
    db.delete(u)
    return {"deleted": username}


# ── Seed ─────────────────────────────────────────────────────────────────────
def ensure_seed_admin(db: Session) -> None:
    """Legt beim Start einen Admin aus WEB_USERNAME/WEB_PASSWORD an, falls es noch
    gar keine Benutzer gibt. So bleibt der bisherige env-Login als erster Admin
    erhalten; danach wird die Verwaltung über die DB gemacht.
    """
    if not settings.WEB_PASSWORD:
        return
    if db.scalar(select(func.count(WebUser.id))) > 0:
        return
    db.add(
        WebUser(
            username=settings.WEB_USERNAME,
            password_hash=hash_password(settings.WEB_PASSWORD),
            role="admin",
            is_active=True,
        )
    )
    db.commit()
