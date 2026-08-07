"""Rendert den (HTML-)Inhalt einer Roh-Mail zu einem PDF (WeasyPrint).

Serverseitige Portierung von ``app/render.py`` des imap-archiver-Clients, damit
die client-API Mails als PDF ausliefern kann. WeasyPrint ist optional (native Libs
pango/cairo); fehlt es, meldet der Endpoint sauber 503 statt zu crashen.
"""

from __future__ import annotations

import base64
import logging
import re
from email import message_from_bytes
from email.message import Message
from html import escape

from app.mailparse import decode, decode_text

try:
    import weasyprint  # noqa: F401

    WEASYPRINT_OK = True
    WEASYPRINT_ERROR: str | None = None
except Exception as _exc:  # ModuleNotFoundError oder fehlende native Libs
    WEASYPRINT_OK = False
    WEASYPRINT_ERROR = str(_exc)

for _name in ("weasyprint", "fontTools", "fontTools.subset", "fontTools.ttLib"):
    logging.getLogger(_name).setLevel(logging.ERROR)

_CID_RE = re.compile(r"""(?i)(src\s*=\s*["']?)cid:([^"'>\s]+)""")


def _collect(msg: Message) -> tuple[list[str], list[str], dict[str, tuple[str, bytes]]]:
    html_parts: list[str] = []
    text_parts: list[str] = []
    inline: dict[str, tuple[str, bytes]] = {}
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        cid = part.get("Content-ID")
        if cid:
            payload = part.get_payload(decode=True) or b""
            key = cid.strip().strip("<>").strip().lower()
            if payload and key:
                inline[key] = (ctype, payload)
            continue
        if part.get_content_disposition() == "attachment":
            continue
        if ctype == "text/html":
            html_parts.append(decode_text(part))
        elif ctype == "text/plain":
            text_parts.append(decode_text(part))
    return html_parts, text_parts, inline


def _inline_cids(html: str, inline: dict[str, tuple[str, bytes]]) -> str:
    def repl(m: re.Match) -> str:
        found = inline.get(m.group(2).strip().lower())
        if not found:
            return m.group(0)
        ctype, payload = found
        b64 = base64.b64encode(payload).decode("ascii")
        return f"{m.group(1)}data:{ctype};base64,{b64}"

    return _CID_RE.sub(repl, html)


def _header_block(msg: Message) -> str:
    rows = [
        ("Von", decode(msg.get("From"))),
        ("An", decode(msg.get("To"))),
        ("Cc", decode(msg.get("Cc"))),
        ("Datum", decode(msg.get("Date"))),
        ("Betreff", decode(msg.get("Subject"))),
    ]
    cells = "".join(
        f'<tr><td class="k">{escape(k)}</td><td class="v">{escape(v)}</td></tr>'
        for k, v in rows
        if v
    )
    return f'<table class="mail-header">{cells}</table>'


_CSS = """
@page { size: A4; margin: 1.6cm 1.5cm; }
body { font-family: "Helvetica Neue", Arial, sans-serif; font-size: 11pt;
       color: #1a1a1a; line-height: 1.4; }
.mail-header { width: 100%; border-collapse: collapse;
       border-bottom: 2px solid #2b6cb0; margin-bottom: 14px; padding-bottom: 6px; }
.mail-header td { padding: 2px 6px; vertical-align: top; font-size: 10pt; }
.mail-header td.k { color: #2b6cb0; font-weight: bold; white-space: nowrap; width: 4.5em; }
.mail-header td.v { color: #333; word-break: break-word; }
.mail-body { word-wrap: break-word; }
.mail-body img { max-width: 100%; height: auto; }
.mail-body pre { white-space: pre-wrap; word-wrap: break-word;
       font-family: "SF Mono", Menlo, monospace; font-size: 10pt; }
.mail-body table { max-width: 100%; }
"""


def html_to_pdf(raw: bytes, *, load_remote: bool = False) -> bytes | None:
    """Rendert die Roh-Mail zu PDF-Bytes. None, wenn kein darstellbarer Inhalt da ist."""
    if not WEASYPRINT_OK:
        raise ModuleNotFoundError(WEASYPRINT_ERROR or "weasyprint")
    from weasyprint import HTML, default_url_fetcher

    msg = message_from_bytes(raw)
    html_parts, text_parts, inline = _collect(msg)
    if html_parts:
        content = _inline_cids(max(html_parts, key=len), inline)
    elif any(t.strip() for t in text_parts):
        content = f"<pre>{escape(chr(10).join(t for t in text_parts if t))}</pre>"
    else:
        return None

    document = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{_CSS}</style></head><body>"
        f'{_header_block(msg)}<div class="mail-body">{content}</div>'
        "</body></html>"
    )

    def fetcher(url: str, *args, **kwargs):
        if url.lower().startswith(("http://", "https://")) and not load_remote:
            raise ValueError("remote resource blocked")
        return default_url_fetcher(url, *args, **kwargs)

    return HTML(string=document, url_fetcher=fetcher).write_pdf()
