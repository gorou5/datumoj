"""PDF text extraction with one searchable chunk per page."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from datetime import date
from hashlib import sha1
from urllib.parse import unquote, urlsplit, urlunsplit

import pypdfium2 as pdfium
from pydantic import JsonValue

from datumoj.rikolto.core.budget import BudgetChecker, BudgetExceeded, budget_from_meta
from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult
from datumoj.rikolto.core.models import (
    META_PARENT_SHA256,
    META_PARENT_URL,
    Chunk,
    DocKind,
    Document,
    Snapshot,
    parent_ref,
)
from datumoj.rikolto.text import normalize


def _normalized_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = parts.hostname
    if host is None:
        raise ParseError("PDF 快照 URL 无效")
    try:
        port = parts.port
    except ValueError as exc:
        raise ParseError("PDF 快照 URL 无效") from exc

    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = parts.netloc.rsplit("@", 1)
    userinfo = f"{authority[0]}@" if len(authority) == 2 else ""
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = f"{userinfo}{host.lower()}"
    if port is not None and not default_port:
        netloc += f":{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _usable_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    title = normalize(value)
    if not title or "\ufffd" in title or any(unicodedata.category(char) == "Cc" for char in title):
        return None
    if any(marker in title for marker in ("Ã", "Â", "â€", "ï¿½", "縺", "繧")):
        return None
    return title


def _title(metadata: Mapping[str, object], first_page_text: str, resource_url: str) -> str:
    title = _usable_title(metadata.get("Title"))
    if title:
        return title
    for line in first_page_text.splitlines():
        title = normalize(line)
        if title and any(char.isalpha() for char in title):
            return title
    filename = unquote(urlsplit(resource_url).path.rsplit("/", 1)[-1])
    return filename or "document.pdf"


def _raise_pdf_error(exc: Exception) -> None:
    message = str(exc).lower()
    if "password" in message or "encrypt" in message:
        raise ParseError("PDF is encrypted and requires a password") from exc
    raise ParseError("PDF is damaged or cannot be read") from exc


def _metadata_date(source_meta: Mapping[str, JsonValue], field: str) -> date | None:
    value = source_meta.get(field)
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ParseError(f"PDF meta 的 {field} 无效：{value!r}") from exc


def _close(resource: object | None) -> None:
    if resource is not None:
        try:
            resource.close()  # type: ignore[attr-defined]
        except Exception:
            pass


class PdfParser(Parser):
    name = "pdf"
    version = "2"
    source_kind = "gov_web"
    content_type = "application/pdf"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        if snapshot.sha256 is None:
            raise ParseError("PDF 快照没有正文")
        checker = BudgetChecker(budget_from_meta(meta))
        checker.check_time()

        try:
            pdf = pdfium.PdfDocument(data)
        except Exception as exc:
            _raise_pdf_error(exc)

        page_texts: list[str] = []
        first_page_text = ""
        metadata: Mapping[str, object]
        try:
            page_count = len(pdf)
            if page_count < 1:
                raise ParseError("PDF 没有页面")
            checker.check_pages(page_count)
            metadata = pdf.get_metadata_dict()
            for page_number in range(1, page_count + 1):
                checker.add_page()
                page = textpage = None
                try:
                    page = pdf[page_number - 1]
                    textpage = page.get_textpage()
                    page_text = textpage.get_text_range()
                    normalized_text = normalize(page_text)
                    checker.add_text(len(normalized_text))
                    if page_number == 1:
                        first_page_text = page_text
                    page_texts.append(normalized_text)
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    _raise_pdf_error(exc)
                finally:
                    _close(textpage)
                    _close(page)
        except ParseError:
            raise
        except Exception as exc:
            _raise_pdf_error(exc)
        finally:
            _close(pdf)

        doc_key = f"{snapshot.source_id}:pdf:{sha1(_normalized_url(snapshot.resource_url).encode('utf-8')).hexdigest()[:16]}"
        title = _title(metadata, first_page_text, snapshot.resource_url)
        source_meta = meta or {}
        document_metadata: dict[str, str] = {
            "pages": str(len(page_texts)),
        }
        published_at = _metadata_date(source_meta, "published_at")
        effective_at = _metadata_date(source_meta, "effective_at")
        if published_at is not None:
            document_metadata["published_at"] = published_at.isoformat()
        if effective_at is not None:
            document_metadata["effective_at"] = effective_at.isoformat()

        chunks: list[Chunk] = []
        warnings: list[str] = []
        needs_ocr_pages = 0
        for page_number, text in enumerate(page_texts, 1):
            checker.check_time()
            if len("".join(text.split())) < 50:
                needs_ocr_pages += 1
                warnings.append(f"needs_ocr page={page_number}")
                continue
            locator = f"page={page_number}"
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_key}#{locator}",
                    doc_key=doc_key,
                    locator=locator,
                    text=text,
                    title_path=[title],
                    snapshot_sha256=snapshot.sha256,
                    lang="ja",
                    published_at=published_at,
                    effective_at=effective_at,
                )
            )
            checker.add_output_chunk()
        document_metadata["needs_ocr_pages"] = str(needs_ocr_pages)
        if page_texts and needs_ocr_pages == len(page_texts):
            warnings.append("needs_ocr all")

        parent = parent_ref(source_meta)
        if parent is not None:
            document_metadata[META_PARENT_URL] = parent[0]
            document_metadata[META_PARENT_SHA256] = parent[1]

        document = Document(
            doc_key=doc_key,
            source_id=snapshot.source_id,
            snapshot_sha256=snapshot.sha256,
            title=title,
            lang="ja",
            kind=DocKind.pdf,
            content_type="application/pdf",
            published_at=published_at,
            effective_at=effective_at,
            metadata=document_metadata,
        )
        return ParseResult(documents=[document], chunks=chunks, warnings=warnings)
