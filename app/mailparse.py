"""Kleine RFC822-Helfer für die client-API (Anhänge, Decode, Betreff).

Serverseitige Portierung der entsprechenden Stellen aus ``app/extract.py`` des
imap-archiver-Clients — damit PDF-Export und Anhang-Download aus der zentralen
Roh-Mail funktionieren, ohne den Client zu benötigen.
"""

from __future__ import annotations

from collections.abc import Iterator
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message


def decode(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — bei kaputten Headern Rohwert behalten
        return value


def decode_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, ValueError):
        return payload.decode("utf-8", errors="replace")


def subject_of(raw: bytes) -> str | None:
    return decode(message_from_bytes(raw).get("Subject"))


def iter_attachments(raw: bytes) -> Iterator[tuple[str | None, str, bytes]]:
    """Liefert (dateiname, content_type, bytes) je Anhang der Roh-Mail."""
    msg = message_from_bytes(raw)
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if disposition == "attachment" or filename:
            payload = part.get_payload(decode=True) or b""
            yield decode(filename), part.get_content_type(), payload
