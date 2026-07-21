"""Bearer-Auth und Passwort-Verschlüsselung (Fernet).

Der Client schickt ``Authorization: Bearer <REST_API_TOKEN>``; ``require_token``
prüft es. IMAP-Passwörter liegen ausschließlich als Fernet-Token in der DB und
werden nur über ``GET /accounts/{name}/credentials`` wieder entschlüsselt.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Header, HTTPException, status

from app.config import settings


def require_token(authorization: str = Header(default="")) -> None:
    if not settings.API_TOKEN:
        raise HTTPException(status_code=500, detail="API_TOKEN ist serverseitig nicht gesetzt.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiges oder fehlendes Bearer-Token.",
        )


@lru_cache
def _cipher() -> Fernet:
    if not settings.SECRET_KEY:
        raise HTTPException(status_code=500, detail="SECRET_KEY ist serverseitig nicht gesetzt.")
    try:
        return Fernet(settings.SECRET_KEY.encode())
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail=f"SECRET_KEY ungültig: {exc}") from exc


def encrypt_password(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt_password(token: str) -> str:
    try:
        return _cipher().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(
            status_code=500,
            detail="Passwort konnte nicht entschlüsselt werden (SECRET_KEY geändert?).",
        ) from exc
