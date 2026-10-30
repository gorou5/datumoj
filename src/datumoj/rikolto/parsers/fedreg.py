"""Federal Register structured XML, with section and paragraph provenance."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from xml.etree import ElementTree as ET
from xml.parsers import expat

from pydantic import JsonValue

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import Chunk, Document, Snapshot, format_locator

MAX_XML_BYTES = 16 * 1024 * 1024
MAX_CHUNK_CHARS = 2000


def _reject_dtd(*_args):
    raise ParseError("Federal Register XML forbids DTD/entities")


def _xml(data: bytes) -> ET.Element:
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = _reject_dtd
    parser.EntityDeclHandler = _reject_dtd
    parser.ExternalEntityRefHandler = _reject_dtd
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    try:
        parser.Parse(data, True)
        return builder.close()
    except (expat.ExpatError, ValueError) as exc:
        raise ParseError("Invalid Federal Register XML") from exc


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _text(node: ET.Element) -> str:
    # Inline E and page markers must not break a paragraph or discard their tails.
    return " ".join("".join(node.itertext()).split())


def _sections(root: ET.Element):
    """Heading levels come from SOURCE; XML wrappers bound each local heading stack."""
    sections = []

    def visit(parent, inherited):
        headings, paragraphs = [], []

        def path():
            return inherited + [heading for _, heading in headings]

        def flush():
            if paragraphs:
                sections.append((path() or ["PREAMBLE"], paragraphs.copy()))
                paragraphs.clear()

        for child in parent:
            tag = _tag(child)
            if tag == "HD":
                flush()
                source = child.get("SOURCE", "HED")
                level = int(source[2:]) if re.fullmatch(r"HD\d+", source) else 0
                while headings and headings[-1][0] >= level:
                    headings.pop()
                heading = _text(child).rstrip(":").strip()
                if heading:
                    headings.append((level, heading))
            elif tag in {"P", "FP", "AMDPAR", "GPOTABLE"}:
                text = _text(child)
                if text:
                    paragraphs.append(text)
            else:
                flush()
                if tag in {"LSTSUB", "PART", "REGTEXT", "SIG"}:
                    headings[:] = [(level, title) for level, title in headings if level == 0]
                child_path = path()
                if tag in {"PREAMB", "SUPLINF", "PART", "LSTSUB"}:
                    child_path = []
                elif tag == "REGTEXT":
                    child_path = ["REGULATORY TEXT", f"{child.get('TITLE', '')} CFR Part {child.get('PART', '')}".strip()]
                elif tag == "SECTION":
                    title = " ".join(_text(node) for node in child if _tag(node) in {"SECTNO", "SUBJECT"})
                    child_path += [title or "SECTION"]
                elif tag == "SIG":
                    child_path = ["SIGNATURE"]
                    text = _text(child)
                    if text:
                        sections.append((child_path, [text]))
                    continue
                visit(child, child_path)
        flush()

    visit(root, [])
    return sections


class FedregParser(Parser):
    name = "fedreg-xml"
    version = "1"
    source_kind = "fedreg"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        if len(data) > MAX_XML_BYTES:
            raise ParseError("Federal Register input exceeds size limit")
        # Inspect the bytes even when headers claim a different format.
        if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{"):
            try:
                payload = json.loads(data)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ParseError("Invalid Federal Register JSON") from exc
            if isinstance(payload, dict) and (isinstance(payload.get("results"), list) or "document_number" in payload):
                return ParseResult()  # Discovery snapshots never duplicate full-text chunks.
            raise ParseError("Unrecognized Federal Register JSON")
        root = _xml(data)
        if _tag(root) not in {"RULE", "PRORULE", "NOTICE"}:
            raise ParseError("Excluded or unrecognized Federal Register XML root")
        info = dict(meta or {})
        number = info.get("document_number")
        if not isinstance(number, str) or not re.fullmatch(r"\d{4}-\d+", number):
            raise ParseError("Federal Register document_number is required")
        title = info.get("title")
        if not isinstance(title, str) or not title.strip() or snapshot.sha256 is None:
            raise ParseError("Federal Register title and snapshot body are required")
        pub = info.get("publication_date")
        try:
            published = date.fromisoformat(pub) if pub is not None else None
        except (TypeError, ValueError) as exc:
            raise ParseError("Invalid Federal Register publication_date") from exc
        embedded = next((_text(node) for node in root if _tag(node) == "FRDOC"), "")
        match = re.search(r"FR Doc\.\s*(\d{4}-\d+)", embedded)
        if match and match[1] != number:
            raise ParseError("Federal Register XML document number mismatch")
        metadata = {
            key: value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            for key, value in info.items()
            if key in {"document_number", "html_url", "type", "agencies", "cfr_references", "citation"}
        }
        if published is None:
            metadata["time_basis"] = "no_data_date"
        document = Document(
            doc_key=f"{snapshot.source_id}:{number}", source_id=snapshot.source_id,
            snapshot_sha256=snapshot.sha256, title=title, lang="en", content_type="application/xml",
            published_at=published, metadata=metadata,
        )
        chunks = []
        for section_index, (path, paragraphs) in enumerate(_sections(root), 1):
            # Small sections stay whole; long sections split only at paragraph boundaries.
            parts, current = [], ""
            for paragraph in paragraphs:
                if current and len(current) + len(paragraph) + 2 > MAX_CHUNK_CHARS:
                    parts.append(current)
                    current = ""
                current = f"{current}\n\n{paragraph}" if current else paragraph
            if current:
                parts.append(current)
            for part_index, text in enumerate(parts, 1):
                locator = format_locator([("section", " > ".join(path)), ("item", section_index), ("para", part_index)])
                chunks.append(Chunk(
                    chunk_id=f"{document.doc_key}#{locator}", doc_key=document.doc_key,
                    locator=locator, text=text, title_path=path, snapshot_sha256=snapshot.sha256,
                    lang="en", published_at=published,
                ))
        if not chunks:
            raise ParseError("Federal Register XML has no section text")
        return ParseResult(documents=[document], chunks=chunks)
