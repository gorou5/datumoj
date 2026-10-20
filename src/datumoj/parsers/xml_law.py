"""Parse e-Gov law XML into article-level, citation-stable chunks."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from datetime import date
from xml.parsers import expat
from xml.etree import ElementTree as ET

from pydantic import JsonValue

from datumoj.core.contracts import ParseError, ParseResult, Parser
from datumoj.core.models import Chunk, Document, Snapshot, format_locator
from datumoj.text import article_number

MAX_XML_BYTES = 16 * 1024 * 1024
MAX_CHUNK_CHARS = 2000
_TITLE_CONTAINERS = {"Part", "Chapter", "Section", "Subsection", "Division"}


class LawXMLParser(Parser):
    name = "egov-law-xml"
    version = "1"
    source_kind = "egov_law"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        if snapshot.sha256 is None:
            raise ParseError(f"快照 {snapshot.snapshot_id} 没有正文")
        if snapshot.source_id != "jp-egov-law":
            raise ParseError("法令快照 source_id 必须是 jp-egov-law")
        if len(data) > MAX_XML_BYTES:
            raise ParseError("法令输入超过大小上限")

        law_meta = meta or {}
        law_id = law_meta.get("law_id")
        if not isinstance(law_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", law_id):
            raise ParseError("meta 缺少合法的 law_id")
        asof = law_meta.get("asof")
        effective_at = None
        if asof is not None:
            if not isinstance(asof, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", asof):
                raise ParseError("meta.asof 必须是 YYYY-MM-DD")
            try:
                effective_at = date.fromisoformat(asof)
            except ValueError as exc:
                raise ParseError("meta.asof 不是有效日期") from exc
        pending_amendments = law_meta.get("pending_amendments", [])
        if not isinstance(pending_amendments, list) or any(not isinstance(item, dict) for item in pending_amendments):
            raise ParseError("meta.pending_amendments 必须是对象列表")

        xml_data, payload_law_id = _xml_bytes(data)
        if len(xml_data) > MAX_XML_BYTES:
            raise ParseError("法令 XML 超过大小上限")
        if payload_law_id is not None and payload_law_id != law_id:
            raise ParseError("meta.law_id 与 XML 响应中的法令 ID 不一致")
        root = _parse_xml(xml_data)
        if _local(root.tag) != "Law":
            raise ParseError("XML 根元素不是 Law")

        law_num = _text(_child(root, "LawNum"))
        law_body = _child(root, "LawBody")
        title = _text(_child(law_body, "LawTitle")) if law_body is not None else ""
        if not law_num or not title:
            raise ParseError("法令 XML 缺少 LawNum 或 LawTitle")

        doc_key = f"jp-egov-law:{law_id}"
        try:
            document = Document(
                doc_key=doc_key,
                source_id=snapshot.source_id,
                snapshot_sha256=snapshot.sha256,
                title=title,
                lang="ja",
                content_type="application/xml",
                effective_at=effective_at,
                metadata={
                    "law_num": law_num,
                    "law_title": title,
                    **({"asof": asof} if asof is not None else {}),
                    "pending_amendments": json.dumps(pending_amendments, ensure_ascii=False),
                },
            )
        except (TypeError, ValueError) as exc:
            raise ParseError("法令 XML 元数据不符合文档契约") from exc
        chunks: list[Chunk] = []

        def add_chunk(locator_parts, text: str, title_path: list[str]) -> None:
            if not text:
                return
            locator = format_locator(locator_parts)
            chunks.append(
                Chunk(
                    chunk_id=f"{doc_key}#{locator}",
                    doc_key=doc_key,
                    locator=locator,
                    text=text,
                    title_path=title_path,
                    snapshot_sha256=snapshot.sha256,
                    lang="ja",
                    effective_at=effective_at,
                )
            )

        table_number = 0

        def visit(parent: ET.Element, path: list[str], supplement: str | None = None) -> None:
            nonlocal table_number
            siblings = list(parent)
            paragraph_number = 0
            for child in siblings:
                kind = _local(child.tag)
                if kind in _TITLE_CONTAINERS:
                    heading = _text(_child(child, f"{kind}Title"))
                    visit(child, path + ([heading] if heading else []), supplement)
                elif kind == "SupplProvision":
                    amend_num = (child.attrib.get("AmendLawNum") or "0").strip() or "0"
                    label = _text(_child(child, "SupplProvisionLabel")) or "附則"
                    visit(child, path + [label], amend_num)
                elif kind == "Article":
                    _add_article(child, path, supplement, add_chunk)
                elif kind == "AppdxTable":
                    table_number += 1
                    parts = [("suppl", supplement)] if supplement is not None else []
                    parts.append(("table", table_number))
                    table_title = _text(_child(child, "AppdxTableTitle"))
                    add_chunk(parts, _text(child), path + ([table_title] if table_title else []))
                elif kind == "Paragraph" and supplement is not None:
                    paragraph_number += 1
                    para_num = child.attrib.get("Num") or str(paragraph_number)
                    add_chunk(
                        [("suppl", supplement), ("para", para_num)],
                        _text(child),
                        path,
                    )
                else:
                    visit(child, path, supplement)

        try:
            visit(root, [title])
            result = ParseResult(documents=[document], chunks=chunks)
        except ParseError:
            raise
        except (TypeError, ValueError) as exc:
            raise ParseError("法令 XML 不能转换为有效的文档块") from exc
        if not chunks:
            raise ParseError("法令 XML 没有可索引条文")
        return result


def _add_article(article: ET.Element, path: list[str], supplement: str | None, add_chunk) -> None:
    article_title = _text(_child(article, "ArticleTitle"))
    raw_number = article.attrib.get("Num", "")
    try:
        number = article_number(article_title) if article_title else _article_attr(raw_number)
    except ValueError:
        try:
            number = _article_attr(raw_number)
        except ValueError as exc:
            raise ParseError("无法识别法令条号") from exc
    parts = [("suppl", supplement)] if supplement is not None else []
    parts.append(("art", number))
    caption = _text(_child(article, "ArticleCaption"))
    article_path = path + ([caption] if caption else []) + ([article_title] if article_title else [])
    article_text = _text(article)
    if len(article_text) <= MAX_CHUNK_CHARS:
        add_chunk(parts, article_text, article_path)
        return

    paragraphs = [child for child in article if _local(child.tag) == "Paragraph"]
    if not paragraphs:
        # shortcut: long articles without Paragraph nodes stay whole, when found split on sentence nodes.
        add_chunk(parts, article_text, article_path)
        return
    for para_index, paragraph in enumerate(paragraphs, 1):
        para_num = paragraph.attrib.get("Num") or str(para_index)
        para_text = _text(paragraph)
        if not para_text:
            continue
        para_parts = parts + [("para", para_num)]
        items = [child for child in paragraph if _local(child.tag) == "Item"]
        if len(para_text) > MAX_CHUNK_CHARS and items:
            header = _text_nodes(child for child in paragraph if _local(child.tag) != "Item")
            add_chunk(para_parts, header, article_path)
            for item_index, item in enumerate(items, 1):
                item_num = item.attrib.get("Num") or str(item_index)
                add_chunk(para_parts + [("item", item_num)], _text(item), article_path)
        else:
            # shortcut: paragraphs over 2000 without Item nodes stay whole, when found split on sentence nodes.
            add_chunk(para_parts, para_text, article_path)


def _article_attr(value: str) -> str:
    if re.fullmatch(r"\d+(?:_\d+|:\d+)*", value):
        return value
    return article_number(value)


def _xml_bytes(data: bytes) -> tuple[bytes, str | None]:
    if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return data, None
    try:
        payload = json.loads(data)
        if not isinstance(payload, dict) or not isinstance(payload.get("law_full_text"), str):
            raise ValueError("missing law_full_text")
        xml_data = base64.b64decode(payload["law_full_text"], validate=True)
        law_info = payload.get("law_info") or {}
        payload_law_id = law_info.get("law_id") if isinstance(law_info, dict) else None
        if payload_law_id is not None and not isinstance(payload_law_id, str):
            raise ValueError("invalid law_id")
        return xml_data, payload_law_id
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ParseError("法令输入不是有效的 XML 或 e-Gov JSON 响应") from exc


def _parse_xml(data: bytes) -> ET.Element:
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
    except ParseError:
        raise
    except (expat.ExpatError, ValueError) as exc:
        raise ParseError(f"法令 XML 损坏：{exc}") from exc


def _reject_dtd(*_args) -> None:
    raise ParseError("法令 XML 禁止 DTD 和实体声明")


def _child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    return next((child for child in parent if _local(child.tag) == name), None)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ET.Element | None) -> str:
    return _text_nodes(element.iter() if element is not None else ())


def _text_nodes(elements) -> str:
    raw = "".join((element.text or "") + (element.tail or "") for element in elements)
    return " ".join(raw.split())
