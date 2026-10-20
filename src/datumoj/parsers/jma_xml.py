"""Safe parser for JMA disaster information XML messages."""

from __future__ import annotations

from datetime import date, datetime
from typing import Mapping

from lxml import etree
from pydantic import JsonValue

from datumoj.core.contracts import ParseError, ParseResult, Parser
from datumoj.core.models import Chunk, Document, Snapshot, format_locator


def _xml_root(data: bytes) -> etree._Element:
    try:
        root = etree.fromstring(
            data,
            etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False),
        )
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ParseError("无法解析 JMA XML") from exc
    if root.getroottree().docinfo.doctype:
        raise ParseError("JMA XML 不允许包含 DTD 或实体声明")
    return root


def _child(element: etree._Element, name: str) -> etree._Element | None:
    return next((child for child in element if isinstance(child.tag, str) and etree.QName(child).localname == name), None)


def _first(root: etree._Element, parent: str, name: str) -> etree._Element | None:
    section = next((item for item in root if isinstance(item.tag, str) and etree.QName(item).localname == parent), None)
    return None if section is None else _child(section, name)


def _text(element: etree._Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _date(value: str) -> date:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError as exc:
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ParseError(f"JMA XML 的 ReportDateTime 无效：{value!r}") from exc


def _area_names(body: etree._Element) -> str:
    names = []
    for area in body.iter():
        if not isinstance(area.tag, str) or etree.QName(area).localname not in {"Area", "TargetArea"}:
            continue
        name = _text(_child(area, "Name"))
        if name and name not in names:
            names.append(name)
    return "、".join(names)


class JmaXmlParser(Parser):
    name = "jma_xml"
    version = "1"
    source_kind = "jma_feed"
    content_type = "*"

    def parse(self, snapshot: Snapshot, data: bytes, meta: Mapping[str, JsonValue] | None = None) -> ParseResult:
        root = _xml_root(data)
        if etree.QName(root).localname == "feed" and etree.QName(root).namespace == "http://www.w3.org/2005/Atom":
            return ParseResult()
        if etree.QName(root).localname != "Report":
            raise ParseError("输入不是 JMA Report XML")

        title = _text(_first(root, "Head", "Title")) or _text(_first(root, "Control", "Title"))
        report_time = _text(_first(root, "Head", "ReportDateTime")) or _text(_first(root, "Control", "DateTime"))
        if not report_time:
            raise ParseError("JMA XML 缺少发表时间")
        body = next((item for item in root if isinstance(item.tag, str) and etree.QName(item).localname == "Body"), None)
        if body is None:
            raise ParseError("JMA XML 缺少正文 Body")
        # shortcut: each report stays one chunk, split if retrieval needs passage-level context.
        body_text = _text(body)
        if not body_text:
            body_text = _text(_first(root, "Head", "Headline"))
        if not title or not body_text:
            raise ParseError("JMA XML 缺少标题或正文")

        entry_id = meta.get("entry_id") if meta else None
        if not isinstance(entry_id, str) or not entry_id:
            entry_id = snapshot.resource_url
        locator = format_locator({"entry": entry_id})
        doc_key = f"{snapshot.source_id}:{entry_id}"
        publisher = _text(_first(root, "Control", "PublishingOffice"))
        areas = _area_names(body)
        metadata = {key: value for key, value in {
            "published_at": report_time,
            "publishing_office": publisher,
            "target_area": areas,
        }.items() if value}
        document = Document(
            doc_key=doc_key,
            source_id=snapshot.source_id,
            snapshot_sha256=snapshot.sha256,
            title=title,
            lang="ja",
            content_type="application/xml",
            published_at=_date(report_time),
            metadata=metadata,
        )
        chunk = Chunk(
            chunk_id=f"{doc_key}#{locator}",
            doc_key=doc_key,
            locator=locator,
            text=body_text,
            title_path=[title],
            snapshot_sha256=snapshot.sha256,
            lang="ja",
            published_at=document.published_at,
        )
        return ParseResult(documents=[document], chunks=[chunk])
