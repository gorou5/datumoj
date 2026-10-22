"""Extract readable, section-addressable text from Japanese government HTML pages."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from lxml import etree, html
from pydantic import JsonValue

from datumoj.core.contracts import ParseError, ParseResult, Parser
from datumoj.core.models import (
    META_PARENT_SHA256,
    META_PARENT_URL,
    Chunk,
    DocKind,
    Document,
    Snapshot,
    format_locator,
    parent_ref,
)
from datumoj.text import decode_bytes, normalize, wareki_to_date

MAX_CHUNK_CHARS = 1500
MIN_CONTENT_CHARS = 100

# Centralized removal rules for common page chrome and navigation wrappers.
HTML_REMOVAL_RULES = {
    "tags": frozenset({"script", "style", "noscript", "nav", "header", "footer", "aside", "form"}),
    "roles": frozenset({"navigation"}),
    "id_class_patterns": (
        re.compile(r"breadcrumb|bread[-_]?crumb|pankuzu|bread|gnav|globalnav|global[-_]?nav|sidebar|side[-_]?menu", re.I),
        re.compile(r"navigation|nav|navi|menu|footer|header|pagetop|page[-_]?top|skip", re.I),
    ),
}

_BLOCK_TAGS = frozenset({"p", "li", "td", "th", "blockquote", "pre", "dt", "dd", "address"})
_CONTAINER_TAGS = frozenset({"div", "section", "article", "main", "ul", "ol", "dl", "table", "tbody", "thead", "tfoot", "tr"})
_UPDATE_LABELS = ("最終更新日", "更新日", "最終更新", "更新")
_OTHER_DATE_LABELS = ("掲載日", "公開日", "作成日")
_PAGE_DATE_BLOCKS = {
    "class": frozenset({"page_day01"}),
    "id": frozenset(),
}
_DATE_TOKEN = (
    r"(?:令和|平成|昭和|大正)\s*(?:元|\d+|[〇零一二三四五六七八九十百千万億兆]+)年\s*"
    r"\d+月\s*\d+日"
    r"|\d{4}\s*(?:年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|[-/]\s*\d{1,2}\s*[-/]\s*\d{1,2})"
)
_DATE_RE = re.compile(_DATE_TOKEN)
_URL_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_LABELED_DATE_RE = re.compile(
    rf"(?P<label>{'|'.join(map(re.escape, (*_UPDATE_LABELS, *_OTHER_DATE_LABELS)))})"
    rf"\s*[：:]?\s*(?P<date>{_DATE_TOKEN})"
)
_META_CHARSET_RE = re.compile(rb"<meta\b[^>]*\bcharset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)", re.I)
_META_CONTENT_CHARSET_RE = re.compile(
    rb"<meta\b[^>]*\bcontent\s*=\s*['\"][^'\"]*?charset\s*=\s*([A-Za-z0-9._-]+)", re.I
)


class HtmlPageParser(Parser):
    name = "gov-web-html-page"
    version = "2"
    source_kind = "gov_web"
    content_type = "text/html"

    def parse(
        self,
        snapshot: Snapshot,
        data: bytes,
        meta: Mapping[str, JsonValue] | None = None,
    ) -> ParseResult:
        if snapshot.sha256 is None:
            raise ParseError(f"快照 {snapshot.snapshot_id} 没有正文")

        try:
            decoded, _, _ = decode_bytes(data, _declared_encoding(data))
            decoded = re.sub(r"^\s*<\?xml\b[^>]*\?>", "", decoded, count=1, flags=re.I)
            root = html.document_fromstring(
                decoded,
                parser=html.HTMLParser(no_network=True, remove_comments=True, recover=True, huge_tree=False),
            )
        except (UnicodeError, ValueError, etree.ParserError) as exc:
            raise ParseError(f"HTML 页面无法解析：{exc}") from exc

        _remove_page_chrome(root)
        content = _content_root(root)
        title = _page_title(root, content)
        if len(_text(content)) < MIN_CONTENT_CHARS:
            raise ParseError("empty_content: HTML 页面正文少于 100 字")

        page_text = _text(root)
        published_at = _published_at(root, page_text)
        doc_key = _doc_key(snapshot)
        document_metadata: dict[str, str] = {}
        if meta is not None:
            relation = parent_ref(meta)
            if relation is not None:
                document_metadata[META_PARENT_URL] = meta[META_PARENT_URL]  # type: ignore[assignment]
                document_metadata[META_PARENT_SHA256] = meta[META_PARENT_SHA256]  # type: ignore[assignment]
            if "seed" in meta:
                value = meta["seed"]
                if isinstance(value, str):
                    document_metadata["seed"] = value
                elif isinstance(value, bool):
                    document_metadata["seed"] = "true" if value else "false"
                elif isinstance(value, int | float):
                    document_metadata["seed"] = str(value)

        try:
            document = Document(
                doc_key=doc_key,
                source_id=snapshot.source_id,
                snapshot_sha256=snapshot.sha256,
                title=title,
                lang="ja",
                kind=DocKind.html_page,
                content_type=self.content_type,
                published_at=published_at,
                metadata=document_metadata,
            )
            chunks = _make_chunks(doc_key, snapshot, content, title, published_at)
            result = ParseResult(documents=[document], chunks=chunks)
            result.check_against(snapshot)
            return result
        except ParseError:
            raise
        except (TypeError, ValueError) as exc:
            raise ParseError(f"HTML 页面不能转换为有效的文档块：{exc}") from exc


def _declared_encoding(data: bytes) -> str | None:
    sample = data[:8192]
    match = _META_CHARSET_RE.search(sample) or _META_CONTENT_CHARSET_RE.search(sample)
    return match.group(1).decode("ascii") if match is not None else None


def _remove_page_chrome(root) -> None:
    for element in list(root.iter()):
        tag = element.tag.lower() if isinstance(element.tag, str) else ""
        roles = set(normalize(element.get("role", "")).lower().split())
        id_class = f"{element.get('id', '')} {element.get('class', '')}"
        if (
            tag in HTML_REMOVAL_RULES["tags"]
            or roles.intersection(HTML_REMOVAL_RULES["roles"])
            or any(pattern.search(id_class) for pattern in HTML_REMOVAL_RULES["id_class_patterns"])
        ):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)


def _element_text(element) -> str:
    return normalize(" ".join(element.itertext()))


def _content_root(root):
    primary = root.xpath(".//main | .//*[@role='main']")
    if primary:
        return max(primary, key=lambda element: len(_element_text(element)))
    blocks = root.xpath(".//article | .//section | .//div")
    if blocks:
        return max(blocks, key=lambda element: len(_element_text(element)))
    bodies = root.xpath(".//body")
    return bodies[0] if bodies else root


def _page_title(root, content) -> str:
    headings = content.xpath(".//h1") or root.xpath(".//h1")
    if headings:
        heading = _element_text(headings[0])
        if heading:
            return heading
    titles = root.xpath(".//title")
    return _element_text(titles[0]) if titles and _element_text(titles[0]) else "HTMLページ"


def _text(root) -> str:
    return normalize(" ".join(root.itertext()))


def _content_events(root):
    """Yield heading and paragraph events in source order, without flattening headings."""
    def walk(element, is_root: bool = False):
        tag = element.tag.lower() if isinstance(element.tag, str) else ""
        if re.fullmatch(r"h[1-3]", tag):
            heading = _element_text(element)
            if heading:
                yield "heading", int(tag[1]), heading
            return
        if tag in _BLOCK_TAGS and not is_root:
            text = _element_text(element)
            if text:
                yield "paragraph", text
            return

        fragments: list[str] = []

        def flush():
            text = normalize(" ".join(fragments))
            fragments.clear()
            if text:
                return ("paragraph", text)
            return None

        if element.text:
            fragments.append(element.text)
        for child in element:
            child_tag = child.tag.lower() if isinstance(child.tag, str) else ""
            if re.fullmatch(r"h[1-3]", child_tag) or child_tag in _BLOCK_TAGS or child_tag in _CONTAINER_TAGS:
                event = flush()
                if event:
                    yield event
                yield from walk(child)
            else:
                inline_text = _element_text(child)
                if inline_text:
                    fragments.append(inline_text)
            if child.tail:
                fragments.append(child.tail)
        event = flush()
        if event:
            yield event

    yield from walk(root, is_root=True)


def _make_chunks(doc_key: str, snapshot: Snapshot, content, title: str, published_at: date | None) -> list[Chunk]:
    sections: list[tuple[list[str], list[str]]] = []
    heading_stack: dict[int, str] = {}
    current_path: list[str] = []
    fallback_path = [title or "正文"]

    for event in _content_events(content):
        if event[0] == "heading":
            _, level, heading = event
            heading_stack = {depth: value for depth, value in heading_stack.items() if depth < level}
            heading_stack[level] = heading
            current_path = [heading_stack[depth] for depth in sorted(heading_stack)]
            continue
        path = current_path or fallback_path
        if not sections or sections[-1][0] != path:
            sections.append((list(path), []))
        sections[-1][1].append(event[1])

    chunks: list[Chunk] = []
    for path, paragraphs in sections:
        if not paragraphs:
            continue
        section_text = "\n\n".join(paragraphs)
        if len(section_text) <= MAX_CHUNK_CHARS:
            groups = [(section_text, None)]
        else:
            groups = []
            pending: list[str] = []
            for paragraph in paragraphs:
                joined = "\n\n".join((*pending, paragraph))
                if pending and len(joined) > MAX_CHUNK_CHARS:
                    groups.append(("\n\n".join(pending), len(groups) + 1))
                    pending = [paragraph]
                else:
                    pending.append(paragraph)
            if pending:
                groups.append(("\n\n".join(pending), len(groups) + 1))

        for text, paragraph_number in groups:
            parts: list[tuple[str, str | int]] = [("section", " > ".join(path))]
            if paragraph_number is not None:
                parts.append(("para", paragraph_number))
            locator = format_locator(parts)
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_key}#{locator}",
                    doc_key=doc_key,
                    locator=locator,
                    text=text,
                    title_path=path,
                    snapshot_sha256=snapshot.sha256,
                    lang="ja",
                    published_at=published_at,
                )
            )
    return chunks


def _published_at(root, page_text: str) -> date | None:
    candidates: list[date] = []
    normalized_text = normalize(page_text)
    for match in _LABELED_DATE_RE.finditer(normalized_text):
        if match.group("label") in _UPDATE_LABELS:
            parsed = _parse_date(match.group("date"))
            if parsed is not None:
                candidates.append(parsed)

    for element in root.xpath(".//meta"):
        label = normalize(" ".join(element.get(key, "") for key in ("name", "property", "itemprop"))).lower()
        if any(term in label for term in ("update", "modified", "最終更新", "更新")):
            value = element.get("content", "")
            for match in _DATE_RE.finditer(normalize(value)):
                parsed = _parse_date(match.group())
                if parsed is not None:
                    candidates.append(parsed)
                    break

    for element in root.iter():
        if (
            _PAGE_DATE_BLOCKS["id"].intersection({element.get("id", "")})
            or _PAGE_DATE_BLOCKS["class"].intersection(element.get("class", "").split())
        ):
            parsed = _parse_date(_element_text(element))
            if parsed is not None:
                candidates.append(parsed)
    return max(candidates) if candidates else None


def _parse_date(token: str) -> date | None:
    normalized = normalize(token)
    if normalized.startswith(("令和", "平成", "昭和", "大正")):
        try:
            parsed = wareki_to_date(normalized)
        except ValueError:
            return None
        if parsed is None or parsed.month is None or parsed.day is None:
            return None
        try:
            return date(parsed.year, parsed.month, parsed.day)
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", normalized)
    if match is None:
        match = re.fullmatch(r"(\d{4})[-/]\s*(\d{1,2})[-/]\s*(\d{1,2})", normalized)
    if match is None:
        return None
    try:
        return date(*(int(value) for value in match.groups()))
    except ValueError:
        return None


def _doc_key(snapshot: Snapshot) -> str:
    parts = urlsplit(snapshot.resource_url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ParseError("HTML 快照 URL 无效")
    scheme = parts.scheme.lower()
    try:
        hostname = parts.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ParseError("HTML 快照 URL 主机名无效") from exc
    try:
        port = parts.port
    except ValueError as exc:
        raise ParseError("HTML 快照 URL 端口无效") from exc
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    path = _normalize_url_path(parts.path or "/")
    query = _normalize_percent_encoding(parts.query)
    normalized_url = urlunsplit((scheme, netloc, path, query, ""))
    digest = hashlib.sha1(normalized_url.encode("utf-8")).hexdigest()[:16]
    return f"{snapshot.source_id}:page:{digest}"


def _normalize_percent_encoding(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        byte = int(match.group(1), 16)
        char = chr(byte)
        return char if char in _URL_UNRESERVED else f"%{byte:02X}"

    return re.sub(r"%([0-9a-fA-F]{2})", replace, value)


def _normalize_url_path(path: str) -> str:
    path = _normalize_percent_encoding(path)
    segments: list[str] = []
    raw_segments = path.split("/")
    for index, segment in enumerate(raw_segments):
        if segment == ".":
            if index == len(raw_segments) - 1:
                segments.append("")
        elif segment == "..":
            if len(segments) > 1 and segments[-1] != "":
                segments.pop()
            if index == len(raw_segments) - 1:
                segments.append("")
        else:
            segments.append(segment)
    normalized = "/".join(segments)
    return normalized if normalized.startswith("/") else f"/{normalized}"
