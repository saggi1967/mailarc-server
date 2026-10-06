"""MQL — mailarc Query Language (Erweiterte Suche, Stufe B1).

Eine kleine, deterministische Suchsprache, die serverseitig in eine Elasticsearch-
``bool``-Query übersetzt wird — derselbe Ausführungspfad wie die Formularsuche
(``run_es_query``). Es gibt keinen zweiten Pfad: MQL → AST → ES-Query.

Umfang v1:
  * Feld-Operatoren ``feld:wert`` bzw. ``feld == wert`` (Synonyme), ``size>…`` / ``size<…``
  * Logik ``AND`` ``OR`` ``NOT`` (+ deutsch ``UND`` ``ODER`` ``NICHT``), Klammern, implizites AND
  * Wertformen: Wort, ``"Phrase"``, ``*``-Wildcard, ``/Regex/`` (Stufe A)
  * deutsche Feld-Aliase (``absender``=from, ``betreff``=subject, ``zeit``=date, …)

Fehler sind ``MqlError`` mit Position + Klartext (das UI zeigt sie inline an).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field as dc_field

from app import es
from app.config import settings

__all__ = ["MqlError", "parse", "compile_query"]


class MqlError(Exception):
    """Syntaktischer/semantischer MQL-Fehler mit Position (0-basiert) im Ausdruck."""

    def __init__(self, position: int, message: str) -> None:
        super().__init__(message)
        self.position = position
        self.message = message


# ── Felder ────────────────────────────────────────────────────────────────────
# kind steuert die Übersetzung; es_field ist das Zielfeld im Index (falls relevant).
FIELDS: dict[str, tuple[str, str | None]] = {
    "from": ("addr", "from_addr"), "absender": ("addr", "from_addr"),
    "to": ("addr", "to"), "an": ("addr", "to"),
    "empfänger": ("addr", "to"), "empfaenger": ("addr", "to"),
    "cc": ("addr", "cc"),
    "anyaddr": ("anyaddr", None),
    "domain": ("domain", "from_domain"),
    "subject": ("text", "subject"), "betreff": ("text", "subject"),
    "body": ("text", "body"), "inhalt": ("text", "body"),
    "text": ("multitext", None),
    "attachtext": ("text", "attachment_text"), "anhangtext": ("text", "attachment_text"),
    "filename": ("filename", "attachments.filename"), "dateiname": ("filename", "attachments.filename"),
    "filetype": ("filetype", "attachments.filename"),
    "has": ("has", None), "anhang": ("has", None),
    "mailbox": ("mailbox", "mailbox"), "folder": ("mailbox", "mailbox"), "ordner": ("mailbox", "mailbox"),
    "size": ("size", "size"),
    "date": ("date", "date"), "zeit": ("date", "date"),
    "before": ("date_before", "date"), "after": ("date_after", "date"),
    "account": ("account", None),
}

AND_WORDS = {"AND", "UND"}
OR_WORDS = {"OR", "ODER"}
NOT_WORDS = {"NOT", "NICHT"}
_LOGIC = AND_WORDS | OR_WORDS | NOT_WORDS

_TEXT_FIELDS = ["subject^3", "from_name^2", "body", "attachment_text"]


# ── Tokenizer ──────────────────────────────────────────────────────────────────
@dataclass
class Tok:
    kind: str  # LPAREN RPAREN OP WORD STR REGEX
    text: str
    pos: int


_WORD_STOP = set(' \t\n\r()"/:=<>')


def _tokenize(s: str) -> list[Tok]:
    toks: list[Tok] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            toks.append(Tok("LPAREN", "(", i)); i += 1; continue
        if c == ")":
            toks.append(Tok("RPAREN", ")", i)); i += 1; continue
        if c == '"':
            j = i + 1
            while j < n and s[j] != '"':
                j += 1
            if j >= n:
                raise MqlError(i, "Nicht geschlossene Phrase (\" fehlt).")
            toks.append(Tok("STR", s[i + 1:j], i)); i = j + 1; continue
        if c == "/":
            j = i + 1
            while j < n and s[j] != "/":
                j += 1
            if j >= n:
                raise MqlError(i, "Nicht geschlossener regulärer Ausdruck (/ fehlt).")
            toks.append(Tok("REGEX", s[i + 1:j], i)); i = j + 1; continue
        if s[i:i + 2] == "==":
            toks.append(Tok("OP", "==", i)); i += 2; continue
        if c in ":<>":
            toks.append(Tok("OP", c, i)); i += 1; continue
        j = i
        while j < n and s[j] not in _WORD_STOP:
            j += 1
        if j == i:  # Schutz gegen Endlosschleife bei unerwartetem Zeichen
            raise MqlError(i, f"Unerwartetes Zeichen '{c}'.")
        toks.append(Tok("WORD", s[i:j], i)); i = j
    return toks


# ── AST ──────────────────────────────────────────────────────────────────────
@dataclass
class Value:
    kind: str  # word phrase regex
    text: str


@dataclass
class Term:
    name: str
    op: str
    value: Value
    pos: int


@dataclass
class Freetext:
    value: Value
    pos: int


@dataclass
class Not:
    item: object


@dataclass
class And:
    items: list = dc_field(default_factory=list)


@dataclass
class Or:
    items: list = dc_field(default_factory=list)


# ── Parser (rekursiver Abstieg) ─────────────────────────────────────────────────
class _Parser:
    def __init__(self, toks: list[Tok], length: int) -> None:
        self.toks = toks
        self.i = 0
        self.length = length

    def _peek(self, k: int = 0) -> Tok | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def _next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    @staticmethod
    def _is(t: Tok | None, words: set[str]) -> bool:
        return t is not None and t.kind == "WORD" and t.text.upper() in words

    def parse(self) -> object:
        if not self.toks:
            raise MqlError(0, "Leerer Ausdruck.")
        node = self._or()
        rest = self._peek()
        if rest is not None:
            raise MqlError(rest.pos, f"Unerwartetes '{rest.text}'.")
        return node

    def _or(self) -> object:
        node = self._and()
        items = [node]
        while self._is(self._peek(), OR_WORDS):
            self._next()
            items.append(self._and())
        return Or(items) if len(items) > 1 else node

    def _and(self) -> object:
        items = [self._not()]
        while True:
            t = self._peek()
            if t is None or t.kind == "RPAREN" or self._is(t, OR_WORDS):
                break
            if self._is(t, AND_WORDS):  # explizites AND
                self._next()
            items.append(self._not())
        return And(items) if len(items) > 1 else items[0]

    def _not(self) -> object:
        if self._is(self._peek(), NOT_WORDS):
            self._next()
            return Not(self._term())
        return self._term()

    def _term(self) -> object:
        t = self._peek()
        if t is None:
            raise MqlError(self.length, "Ausdruck erwartet.")
        if t.kind == "LPAREN":
            self._next()
            inner = self._or()
            close = self._peek()
            if close is None or close.kind != "RPAREN":
                raise MqlError(close.pos if close else self.length, "')' erwartet.")
            self._next()
            return inner
        if t.kind in ("RPAREN", "OP"):
            raise MqlError(t.pos, f"Unerwartetes '{t.text}'.")
        # t ist WORD / STR / REGEX
        nxt = self._peek(1)
        if t.kind == "WORD" and nxt is not None and nxt.kind == "OP":
            name = t.text
            op = nxt.text
            self.i += 2
            v = self._peek()
            if v is None or v.kind not in ("WORD", "STR", "REGEX"):
                raise MqlError(v.pos if v else self.length, f"Wert erwartet nach '{name}{op}'.")
            self._next()
            return Term(name, op, _value_of(v), t.pos)
        self._next()
        return Freetext(_value_of(t), t.pos)


def _value_of(t: Tok) -> Value:
    return Value({"STR": "phrase", "REGEX": "regex"}.get(t.kind, "word"), t.text)


# ── Übersetzung AST → ES-Query ──────────────────────────────────────────────────
def _resolve_field(name: str, pos: int) -> tuple[str, str | None]:
    key = name.lower()
    if key in FIELDS:
        return FIELDS[key]
    near = difflib.get_close_matches(key, FIELDS.keys(), n=1)
    hint = f" — meintest du '{near[0]}'?" if near else ""
    raise MqlError(pos, f"Unbekanntes Feld '{name}'{hint}")


def _parse_size(text: str, pos: int) -> int:
    m = re.fullmatch(r"(\d+)([kKmMgG]?)", text)
    if not m:
        raise MqlError(pos, f"Ungültige Größenangabe '{text}' (z. B. 5M, 100K, 2G).")
    mult = {"": 1, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000}[m.group(2).lower()]
    return int(m.group(1)) * mult


_NAMED_RANGES = {"week": "7d", "month": "30d", "quarter": "90d", "year": "365d", "day": "1d"}


def _parse_daterule(text: str, pos: int) -> str:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    t = text.lower()
    if t.startswith("last-"):
        t = t[5:]
    t = _NAMED_RANGES.get(t, t)
    try:
        return es.parse_last(t)
    except ValueError as exc:
        raise MqlError(pos, f"Ungültige Zeitangabe '{text}' (z. B. 2026-01-01 oder last-30d).") from exc


def _regex_or(field: str, value: Value, pos: int, plain) -> dict:
    """Gemeinsam: Regex → regexp_clause (mit Guard→MqlError), sonst ``plain(value)``."""
    if value.kind == "regex":
        try:
            return es.regexp_clause(field, value.text)
        except ValueError as exc:
            raise MqlError(pos, str(exc)) from exc
    return plain(value)


def _term_clause(t: Term) -> dict:
    kind, esf = _resolve_field(t.name, t.pos)
    v = t.value
    has_wild = v.kind == "word" and "*" in v.text

    if kind in ("addr", "domain", "mailbox"):
        lower = kind != "mailbox"
        field = esf  # type: ignore[assignment]
        if kind == "addr" and t.name.lower() in ("from", "absender") and v.kind == "word" and v.text.startswith("@"):
            field = "from_domain"
            return {"term": {field: v.text[1:].lower()}}
        def plain(val: Value, _f=field, _l=lower) -> dict:
            if "*" in val.text and val.kind == "word":
                return {"wildcard": {_f: val.text.lower() if _l else val.text}}
            return {"term": {_f: val.text.lower() if _l else val.text}}
        return _regex_or(field, v, t.pos, plain)

    if kind == "anyaddr":
        clauses = [{"term": {f: v.text.lower()}} for f in ("from_addr", "to", "cc")]
        return {"bool": {"should": clauses, "minimum_should_match": 1}}

    if kind in ("text",):
        def plain(val: Value, _f=esf) -> dict:
            if val.kind == "phrase":
                return {"match_phrase": {_f: val.text}}
            if "*" in val.text:
                return {"wildcard": {_f: val.text.lower()}}
            return {"match": {_f: val.text}}
        return _regex_or(esf, v, t.pos, plain)  # type: ignore[arg-type]

    if kind == "multitext":
        mm = {"query": v.text, "fields": _TEXT_FIELDS}
        if v.kind == "phrase":
            mm["type"] = "phrase"
        return {"multi_match": mm}

    if kind in ("filename", "filetype"):
        pattern = f"*.{v.text.lstrip('.').lower()}" if kind == "filetype" else v.text
        if kind == "filename" and v.kind == "regex":
            inner = _regex_or("attachments.filename", v, t.pos, lambda _v: {})
        elif ("*" in pattern) or kind == "filetype":
            inner = {"wildcard": {"attachments.filename": pattern.lower()}}
        elif v.kind == "phrase":
            inner = {"match_phrase": {"attachments.filename": v.text}}
        else:
            inner = {"match": {"attachments.filename": v.text}}
        return {"nested": {"path": "attachments", "query": inner}}

    if kind == "has":
        yes = v.text.lower() in ("attachment", "anhang", "ja", "yes", "true", "1")
        no = v.text.lower() in ("nein", "no", "false", "0")
        if not (yes or no):
            raise MqlError(t.pos, "has/anhang erwartet 'attachment' bzw. ja/nein.")
        return {"term": {"has_attachment": yes}}

    if kind == "size":
        n = _parse_size(v.text, t.pos)
        if t.op == ">":
            return {"range": {"size": {"gt": n}}}
        if t.op == "<":
            return {"range": {"size": {"lt": n}}}
        return {"term": {"size": n}}

    if kind in ("date", "date_before", "date_after"):
        iso = _parse_daterule(v.text, t.pos)
        bound = "lte" if kind == "date_before" else "gte"
        return {"range": {"date": {bound: iso}}}

    if kind == "account":
        raise MqlError(t.pos, "Der 'account'-Filter kommt mit der Mandantenfähigkeit (noch nicht verfügbar).")

    raise MqlError(t.pos, f"Feld '{t.name}' wird noch nicht unterstützt.")  # pragma: no cover


def _freetext_clause(f: Freetext) -> dict:
    v = f.value
    mm = {"query": v.text, "fields": _TEXT_FIELDS}
    if v.kind == "phrase":
        mm["type"] = "phrase"
    return {"multi_match": mm}


def _to_es(node: object) -> dict:
    if isinstance(node, Or):
        return {"bool": {"should": [_to_es(x) for x in node.items], "minimum_should_match": 1}}
    if isinstance(node, And):
        return {"bool": {"must": [_to_es(x) for x in node.items]}}
    if isinstance(node, Not):
        return {"bool": {"must_not": [_to_es(node.item)]}}
    if isinstance(node, Term):
        return _term_clause(node)
    if isinstance(node, Freetext):
        return _freetext_clause(node)
    raise MqlError(0, "Interner Fehler: unbekannter Knoten.")  # pragma: no cover


# ── Öffentliche API ─────────────────────────────────────────────────────────────
def parse(text: str) -> object:
    """MQL-Text → AST. Wirft ``MqlError`` bei Syntaxfehlern."""
    toks = _tokenize(text)
    return _Parser(toks, len(text)).parse()


def compile_query(text: str) -> dict:
    """MQL-Text → Elasticsearch-``query``-Objekt. Wirft ``MqlError``."""
    if not text or not text.strip():
        raise MqlError(0, "Leere Suchanfrage.")
    return _to_es(parse(text))
