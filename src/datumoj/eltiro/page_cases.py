"""Offline, bounded HTML case extraction and content-only change tracking."""

from __future__ import annotations

import os
import re
import sqlite3
import stat
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from datumoj.eltiro.intake import open_directory
from datumoj.eltiro.models import MAX_TEXT_LENGTH, content_hash

MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_CASES = 256
MAX_DEPTH = 64
MAX_NODES = 50000
FIELDS = {
    "verify": ("hypothesis", "method", "signal"),
    "cases": ("category", "question", "fusion", "aha"),
}
CLASSES = {"no": "number", "hyp": "hypothesis", "method": "method", "signal": "signal",
           "stag": "category", "q": "question", "fuse": "fusion", "aha": "aha"}
VOID = frozenset(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"])


def _text(value: str) -> str:
    return " ".join(value.split())


def _number(value: str, kind: str) -> bool:
    return bool(re.fullmatch(r"V[0-9]{2,3}" if kind == "verify" else r"#[0-9]{3}", value))


@dataclass(frozen=True)
class SourceLink:
    text: str
    url: str


@dataclass(frozen=True)
class PageCase:
    kind: str
    number: str
    title: str
    sources: tuple[SourceLink, ...]
    hypothesis: str = ""
    method: str = ""
    signal: str = ""
    category: str = ""
    question: str = ""
    fusion: str = ""
    aha: str = ""

    def __post_init__(self):
        if self.kind not in FIELDS or not _number(self.number, self.kind):
            raise ValueError("invalid case identity")
        required = (self.title, *(getattr(self, key) for key in FIELDS[self.kind]))
        if not all(required) or not self.sources:
            raise ValueError("missing case fields")
        values = [value for key, value in asdict(self).items() if key != "sources"]
        for source in self.sources:
            if not source.text or not source.url:
                raise ValueError("missing source text or URL")
            values.extend((source.text, source.url))
        if any(not isinstance(value, str) or value != _text(value) or len(value) > MAX_TEXT_LENGTH
               for value in values):
            raise ValueError("invalid case text")
        if any(getattr(self, key) for other, fields in FIELDS.items() if other != self.kind for key in fields):
            raise ValueError("fields belong to a different page kind")

    @property
    def content_hash(self) -> str:
        return content_hash(asdict(self))


@dataclass(frozen=True)
class ParseFailure:
    index: int  # one-based article position
    number: str | None
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ParsedCases:
    kind: str
    cases: tuple[PageCase, ...]
    failures: tuple[ParseFailure, ...]


class _Parser(HTMLParser):
    def __init__(self, kind):
        super().__init__(convert_charrefs=True)
        self.kind = kind
        self.stack = []  # tag, suppressed, capture index, inside .srcs
        self.records = []
        self.current = None
        self.captures = []
        self.errors = []
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > MAX_NODES or len(self.stack) >= MAX_DEPTH:
            raise ValueError("page structure limit exceeded")
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        suppressed = bool(self.stack and self.stack[-1][1]) or tag in ("script", "style", "template")
        if tag == "article" and "case" in classes and not suppressed:
            if self.current is not None:
                self._finish("unclosed article")
            if len(self.records) >= MAX_CASES:
                raise ValueError("too many cases")
            self.current = len(self.stack)
            self.captures = []
            self.errors = []
        suppressed = suppressed or "lab" in classes
        capture = self.stack[-1][2] if self.stack else None
        in_sources = bool(self.stack and self.stack[-1][3]) or "srcs" in classes
        if self.current is not None and not suppressed:
            keys = {CLASSES[cls] for cls in classes if cls in CLASSES}
            if tag == "h3":
                keys.add("title")
            keys &= {"number", "title", *FIELDS[self.kind]}
            if tag == "a" and "src" in classes and in_sources:
                keys.add("sources")
            if keys:
                if len(keys) != 1:
                    self.errors.append("ambiguous field")
                key = min(keys)
                capture = len(self.captures)
                self.captures.append([key, [], attrs.get("href", "") if key == "sources" else ""])
        if tag not in VOID:
            self.stack.append((tag, suppressed, capture, in_sources))
        elif tag == "br":
            self.handle_data(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.current is not None and self.stack and not self.stack[-1][1]:
            index = self.stack[-1][2]
            if index is not None:
                self.captures[index][1].append(data)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        position = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if position is None:
            return
        if self.current is not None and position <= self.current:
            self._finish(None if tag == "article" and position == self.current else "unclosed article")
        del self.stack[position:]

    def _finish(self, error=None):
        fields = {}
        links = []
        errors = self.errors + ([error] if error else [])
        for key, chunks, url in self.captures:
            text = _text("".join(chunks))
            if key == "sources":
                links.append(SourceLink(text, _text(url)))
            elif key in fields:
                errors.append("duplicate " + key)
            else:
                fields[key] = text
        number = fields.get("number", "")
        known_number = number if _number(number, self.kind) and "duplicate number" not in errors else None
        if known_number is None:
            errors.append("missing or invalid number")
        for key in ("title", *FIELDS[self.kind]):
            if not fields.get(key):
                errors.append("missing " + key)
        case = None
        if not errors:
            try:
                case = PageCase(kind=self.kind, sources=tuple(links), **fields)
            except ValueError as exc:
                errors.append(str(exc))
        self.records.append((case, known_number, tuple(errors)))
        self.current = None


def parse_cases(page_bytes: bytes, kind: str) -> ParsedCases:
    """UTF-8 (optional BOM), no I/O. Whole-page errors raise ValueError.

    Missing/invalid/duplicate article fields produce failures instead. CSS labels,
    scripts, styles, templates and all unselected attributes/text are excluded.
    """
    if kind not in FIELDS or not isinstance(page_bytes, bytes):
        raise ValueError("expected bytes and verify/cases kind")
    if len(page_bytes) > MAX_PAGE_BYTES:
        raise ValueError("page too large")
    try:
        text = page_bytes.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("page must be UTF-8") from None
    parser = _Parser(kind)
    parser.feed(text)
    parser.close()
    if parser.current is not None:
        parser._finish("unclosed article")
    if not parser.records:
        raise ValueError("no case articles found")
    counts = Counter(number for _, number, _ in parser.records if number)
    cases, failures = [], []
    for index, (case, number, errors) in enumerate(parser.records, 1):
        if number and counts[number] > 1:
            errors += ("duplicate number",)
        if errors:
            failures.append(ParseFailure(index, number, errors))
        else:
            cases.append(case)
    return ParsedCases(kind, tuple(cases), tuple(failures))


@dataclass(frozen=True)
class CaseChanges:
    added: tuple[str, ...]
    changed: tuple[str, ...]
    disappeared: tuple[str, ...]
    unchanged: tuple[str, ...]
    failures: tuple[ParseFailure, ...]


def record_cases(parsed: ParsedCases, *, data_dir: str | Path, seen_at: datetime) -> CaseChanges:
    """Record valid cases transactionally; retain failed identities and first_seen.

    An unidentified failure protects all unmatched old identities. Missing records
    remain as tombstones (present=0); repeated absence is not reported twice.
    """
    if parsed.kind not in FIELDS or any(case.kind != parsed.kind for case in parsed.cases):
        raise ValueError("page kind mismatch")
    if len({case.number for case in parsed.cases}) != len(parsed.cases):
        raise ValueError("duplicate case identity")
    if not isinstance(seen_at, datetime) or seen_at.utcoffset() is None:
        raise ValueError("seen_at must be timezone aware")
    timestamp = seen_at.isoformat()
    root = Path(data_dir).resolve()
    directory = open_directory(root, root / "eltiro")
    try:
        fd = os.open("page_cases.sqlite", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("invalid case database")
        finally:
            os.close(fd)
        db = sqlite3.connect(root / "eltiro" / "page_cases.sqlite")
        try:
            with db:
                db.execute("""CREATE TABLE IF NOT EXISTS page_cases (
                    kind TEXT NOT NULL, number TEXT NOT NULL, content_hash TEXT NOT NULL,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, present INTEGER NOT NULL,
                    PRIMARY KEY (kind, number))""")
                db.execute("BEGIN IMMEDIATE")
                old = {row[0]: row[1:] for row in db.execute(
                    "SELECT number, content_hash, present FROM page_cases WHERE kind = ?", (parsed.kind,))}
                added, changed, unchanged = [], [], []
                for case in parsed.cases:
                    previous = old.get(case.number)
                    target = added if previous is None else (unchanged if previous[0] == case.content_hash else changed)
                    target.append(case.number)
                    db.execute("""INSERT INTO page_cases VALUES (?, ?, ?, ?, ?, 1)
                        ON CONFLICT(kind, number) DO UPDATE SET content_hash=excluded.content_hash,
                        last_seen=excluded.last_seen, present=1""",
                        (case.kind, case.number, case.content_hash, timestamp, timestamp))
                seen = {case.number for case in parsed.cases} | {failure.number for failure in parsed.failures}
                disappeared = [] if any(f.number is None for f in parsed.failures) else sorted(
                    number for number, (_, present) in old.items() if present and number not in seen)
                for number in disappeared:
                    db.execute("UPDATE page_cases SET present=0 WHERE kind=? AND number=?", (parsed.kind, number))
            return CaseChanges(tuple(sorted(added)), tuple(sorted(changed)), tuple(disappeared),
                               tuple(sorted(unchanged)), parsed.failures)
        finally:
            db.close()
    finally:
        os.close(directory)
