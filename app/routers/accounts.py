"""Zentrale IMAP-Konten: anlegen, auflisten, ändern, entfernen, Credentials liefern.

Gegenstelle zu ``app/accounts.py`` des Clients. Passwörter werden verschlüsselt
gespeichert (``imap_password_enc``) und nur unter ``/credentials`` entschlüsselt.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Account
from app.schemas import AccountCreate, AccountOut, AccountUpdate, CredentialsOut
from app.security import decrypt_password, encrypt_password

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _out(a: Account) -> dict:
    return {
        "name": a.name,
        "imap_host": a.imap_host,
        "imap_port": a.imap_port,
        "imap_ssl": a.imap_ssl,
        "imap_ssl_verify": a.imap_ssl_verify,
        "imap_user": a.imap_user,
        "folders": a.folders,
    }


def _get_or_404(db: Session, name: str) -> Account:
    a = db.scalar(select(Account).where(Account.name == name))
    if a is None:
        raise HTTPException(status_code=404, detail=f"Konto '{name}' nicht gefunden.")
    return a


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_session)) -> list[dict]:
    rows = db.scalars(select(Account).order_by(Account.name)).all()
    return [_out(a) for a in rows]


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(body: AccountCreate, db: Session = Depends(get_session)) -> dict:
    if db.scalar(select(Account).where(Account.name == body.name)):
        raise HTTPException(status_code=409, detail=f"Konto '{body.name}' existiert bereits.")
    a = Account(
        name=body.name,
        imap_host=body.imap_host,
        imap_port=body.imap_port,
        imap_ssl=body.imap_ssl,
        imap_ssl_verify=body.imap_ssl_verify,
        imap_user=body.imap_user,
        imap_password_enc=encrypt_password(body.imap_password),
        folders=body.folders,
    )
    db.add(a)
    db.flush()
    return _out(a)


@router.patch("/{name}", response_model=AccountOut)
def update_account(name: str, body: AccountUpdate, db: Session = Depends(get_session)) -> dict:
    a = _get_or_404(db, name)
    data = body.model_dump(exclude_unset=True)
    if "imap_password" in data:
        a.imap_password_enc = encrypt_password(data.pop("imap_password"))
    for field, value in data.items():
        setattr(a, field, value)
    db.flush()
    return _out(a)


@router.delete("/{name}")
def delete_account(name: str, db: Session = Depends(get_session)) -> dict:
    a = _get_or_404(db, name)
    db.delete(a)
    return {"deleted": name}


@router.get("/{name}/credentials", response_model=CredentialsOut)
def get_credentials(name: str, db: Session = Depends(get_session)) -> dict:
    a = _get_or_404(db, name)
    return {
        "imap_host": a.imap_host,
        "imap_port": a.imap_port,
        "imap_ssl": a.imap_ssl,
        "imap_ssl_verify": a.imap_ssl_verify,
        "imap_user": a.imap_user,
        "imap_password": decrypt_password(a.imap_password_enc),
        "folders": a.folders,
    }
