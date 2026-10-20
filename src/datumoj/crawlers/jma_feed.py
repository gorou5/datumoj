"""Crawler for JMA's long-lived disaster information Atom feeds."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

from lxml import etree

from datumoj.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask
from datumoj.core.models import FetchOutcome

ATOM = "http://www.w3.org/2005/Atom"
FEED_URL = "https://www.data.jma.go.jp/developer/xml/feed/regular_l.xml"


def _xml_root(data: bytes) -> etree._Element:
    try:
        root = etree.fromstring(
            data,
            etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False),
        )
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ValueError("无法解析 JMA Atom feed") from exc
    if root.getroottree().docinfo.doctype:
        raise ValueError("JMA Atom feed 不允许包含 DTD")
    if etree.QName(root).localname != "feed" or etree.QName(root).namespace != ATOM:
        raise ValueError("响应不是 Atom feed")
    return root


def _text(element: etree._Element | None) -> str:
    return "" if element is None else " ".join("".join(element.itertext()).split())


def _feed_entries(data: bytes) -> tuple[str, list[tuple[str, str]]]:
    root = _xml_root(data)
    updated = _text(root.find(f"{{{ATOM}}}updated"))
    if not updated:
        raise ValueError("JMA Atom feed 缺少 updated")

    entries = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        entry_id = _text(entry.find(f"{{{ATOM}}}id"))
        links = entry.findall(f"{{{ATOM}}}link")
        link = next((item for item in links if item.get("type") == "application/xml"), None)
        url = link.get("href", "").strip() if link is not None else entry_id
        if not entry_id or not url:
            raise ValueError("JMA Atom entry 缺少 id 或 XML 链接")
        entries.append((entry_id, url))
    return updated, entries


def _allowed_url(url: str, hosts: list[str]) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443) or parsed.query or parsed.fragment:
            return False
    except ValueError:
        return False
    return any(host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:])) for pattern in hosts)


class JmaFeedCrawler(Crawler):
    """Poll configured Atom feeds and fetch entries not previously saved successfully."""

    kind = "jma_feed"

    def plan(self, ctx: CrawlContext) -> Iterable[FetchTask]:
        feeds = ctx.spec.scope.get("feeds")
        if not isinstance(feeds, list) or not feeds:
            raise ValueError("scope.feeds 必须是非空 URL 列表")
        if not all(isinstance(url, str) and _allowed_url(url, ctx.spec.allowed_hosts) for url in feeds):
            raise ValueError("scope.feeds 只能包含允许 host 上的 HTTPS URL")
        for url in dict.fromkeys(feeds):
            yield FetchTask(url=url, meta={"resource_type": "feed"})

    def after_fetch(self, task: FetchTask, result: FetchResult, ctx: CrawlContext) -> Iterable[FetchTask]:
        if task.meta.get("resource_type") != "feed" or result.outcome is not FetchOutcome.ok:
            return ()

        updated, entries = _feed_entries(result.content or b"")
        previous = ctx.manifest.last_success(task.redacted_url)
        previous_updated = None
        if previous is not None and previous.sha256 is not None:
            previous_root = _xml_root(ctx.raw.get(previous.sha256))
            previous_updated = _text(previous_root.find(f"{{{ATOM}}}updated"))

        saved_ids = {
            metadata["entry_id"]
            for snapshot in ctx.manifest.snapshots_for(ctx.spec.id)
            if snapshot.outcome in (FetchOutcome.ok, FetchOutcome.not_modified)
            if (metadata := ctx.manifest.snapshot_meta(snapshot.snapshot_id)).get("resource_type") == "entry"
            and isinstance(metadata.get("entry_id"), str)
        }
        if previous_updated == updated and not any(entry_id not in saved_ids for entry_id, _ in entries):
            return ()

        scheduled: set[str] = set()
        count = 0
        for entry_id, url in entries:
            if entry_id in saved_ids or entry_id in scheduled:
                continue
            if not _allowed_url(entry_id, ctx.spec.allowed_hosts) or not _allowed_url(url, ctx.spec.allowed_hosts):
                raise ValueError(f"JMA entry id/链接不是允许 host 上的 HTTPS URL：{entry_id!r}")
            if ctx.limit is not None and count >= ctx.limit:
                break
            scheduled.add(entry_id)
            count += 1
            yield FetchTask(url=url, meta={"resource_type": "entry", "entry_id": entry_id, "feed_updated": updated})
