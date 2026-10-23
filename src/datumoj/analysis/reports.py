"""Markdown reports built from the public DATUMOJ contracts."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from datumoj.core.citations import document_snapshot
from datumoj.core.contracts import AmbiguousCitation, citation_for_chunk
from datumoj.core.models import (
    ChangeEvent,
    ChangeKind,
    Citation,
    DocKind,
    Snapshot,
    TimeBasis,
)
from datumoj.core.monitor import Alert


_KINDS = (
    ChangeKind.added,
    ChangeKind.modified,
    ChangeKind.removed,
    ChangeKind.fetch_failed,
    ChangeKind.parse_failed,
)
_KIND_LABELS = {
    ChangeKind.added: "新增",
    ChangeKind.modified: "修改",
    ChangeKind.removed: "删除",
    ChangeKind.fetch_failed: "抓取失败",
    ChangeKind.parse_failed: "解析失败",
}
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|><~])")


def _plain(value: Any) -> str:
    """Render one line of untrusted text without allowing Markdown structure."""
    text = " ".join(str(value).split())
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _boundary(value: date | datetime | None, *, end: bool) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("since/until datetime 必须带时区")
        return value
    return datetime.combine(value, time.max if end else time.min, tzinfo=UTC)


def _period(since: date | datetime | None, until: date | datetime | None) -> tuple[datetime, datetime]:
    end = _boundary(until, end=True) or datetime.now(UTC)
    start = _boundary(since, end=False)
    if start is None:
        if isinstance(until, date) and not isinstance(until, datetime):
            start = datetime.combine(until - timedelta(days=6), time.min, tzinfo=UTC)
        else:
            start = end - timedelta(days=7)
    if start > end:
        raise ValueError("since 不能晚于 until")
    return start, end


def _event_sha(event: ChangeEvent) -> str | None:
    if event.kind is ChangeKind.removed:
        return event.old_sha256
    return event.new_sha256 or event.old_sha256


def _snapshot(deps: Any, event: ChangeEvent) -> Snapshot | None:
    sha = _event_sha(event)
    if sha is None or deps.manifest is None:
        return None
    doc = _document(deps, event, sha)
    return document_snapshot(
        deps.manifest, sha, event.source_id, event.doc_key or "", doc, resource_url=event.resource_url,
    )


def _document(deps: Any, event: ChangeEvent, sha: str | None) -> Any:
    if event.doc_key is None or sha is None or deps.chunks is None:
        return None
    return deps.chunks.get_document(event.doc_key, sha)


def _citation(deps: Any, event: ChangeEvent, snapshot: Snapshot, doc: Any) -> tuple[Citation | None, str | None]:
    chunks = []
    if doc is not None:
        chunks = deps.chunks.chunks_for(doc.doc_key, doc.snapshot_sha256)
    if event.locator is not None:
        chunk = next((item for item in chunks if item.locator == event.locator), None)
        locator = event.locator
    else:
        chunk = chunks[0] if chunks else None
        locator = chunk.locator if chunk is not None else None
    if chunk is not None:
        return citation_for_chunk(chunk, snapshot, _license(deps, event.source_id)), None
    if locator is None:
        return None, "快照已找到，但文档没有可还原的 locator"

    license_info = _license(deps, event.source_id)
    published_at = doc.published_at if doc is not None else None
    effective_at = doc.effective_at if doc is not None else None
    basis = (
        TimeBasis.effective if effective_at is not None else
        TimeBasis.published if published_at is not None else TimeBasis.no_data_date
    )
    return Citation(
        snapshot_id=snapshot.snapshot_id,
        source_id=event.source_id,
        resource_url=snapshot.resource_url,
        snapshot_sha256=snapshot.sha256,
        fetched_at=snapshot.fetched_at,
        locator=locator,
        published_at=published_at,
        effective_at=effective_at,
        time_basis=basis,
        license=license_info.name if license_info else None,
        attribution=license_info.attribution if license_info else None,
        usage_scope=license_info.usage_scope if license_info else None,
    ), None


def _license(deps: Any, source_id: str) -> Any:
    spec = deps.registry.get(source_id) if deps.registry is not None else None
    return spec.license if spec is not None else None


def _title_part(doc: Any) -> str:
    return f"标题：{_plain(doc.title)}；" if doc is not None else ""


def _event_line(deps: Any, event: ChangeEvent) -> str:
    sha = _event_sha(event)
    doc = _document(deps, event, sha)
    try:
        snapshot = _snapshot(deps, event)
    except AmbiguousCitation:
        return f"- {_title_part(doc)}摘要：{_plain(event.summary)}；来源快照有歧义，无法确定资源 URL。"
    text = f"- {_title_part(doc)}摘要：{_plain(event.summary)}；"
    if snapshot is None:
        reference = event.resource_url or event.doc_key or "未知资源"
        failure_label = "失败类型" if event.kind in {ChangeKind.fetch_failed, ChangeKind.parse_failed} else "事件类型"
        return (
            text + f"没有快照，无法还原；资源：{_plain(reference)}；"
            f"检测时间：{_plain(event.detected_at.isoformat())}；{failure_label}：{event.kind.value}。"
        )
    citation, issue = _citation(deps, event, snapshot, doc)
    if citation is None:
        return (
            text + f"{_plain(issue)}；资源：{_plain(snapshot.resource_url)}；"
            f"检测时间：{_plain(event.detected_at.isoformat())}；事件类型：{event.kind.value}。"
        )
    whole_doc = "整份文档引用（位置取首个已存 locator）：" if event.locator is None else "出处："
    return text + f"{whole_doc}{_plain(citation.render())}；snapshot_id：{_plain(citation.snapshot_id)}"


def _ocr_summary(deps: Any, events: Sequence[ChangeEvent]) -> str:
    documents: dict[str, Any] = {}
    for event in events:
        if event.doc_key is None:
            continue
        doc = _document(deps, event, _event_sha(event))
        if doc is not None and doc.kind is DocKind.pdf:
            documents[doc.doc_key] = doc
    page_count = ocr_count = 0
    counted = 0
    for doc in documents.values():
        pages = doc.metadata.get("pages", "")
        needs_ocr = doc.metadata.get("needs_ocr_pages", "")
        if not pages.isdecimal() or not needs_ocr.isdecimal():
            continue
        page_count += int(pages)
        ocr_count += int(needs_ocr)
        counted += 1
    return (
        f"本周有变化的 PDF：needs_ocr {ocr_count}/{page_count} 页；"
        f"统计口径：本周有变化的 PDF（共 {counted} 份，metadata 不完整的文档未计入）。"
    )


def weekly_report(
    deps: Any,
    since: date | datetime | None = None,
    until: date | datetime | None = None,
    alerts: Sequence[Alert | Mapping[str, Any]] = (),
) -> str:
    """Create a source-grouped weekly report using change, manifest, and chunk contracts."""
    start, end = _period(since, until)
    events = deps.change_log.list(since=start, until=end) if deps.change_log is not None else []
    counts = {
        kind: Counter(event.source_id for event in events if event.kind is kind)
        for kind in _KINDS
    }
    source_ids = {spec.id for spec in deps.registry.all()} if deps.registry is not None else set()
    source_ids.update(event.source_id for event in events)
    specs = {spec.id: spec for spec in deps.registry.all()} if deps.registry is not None else {}
    lines = [
        "# DATUMOJ 每周变化报告",
        "",
        f"统计区间：{_plain(start.isoformat())} 至 {_plain(end.isoformat())}",
        "",
        "## 总览",
        "",
        "| 来源 | 新增 | 修改 | 删除 | 抓取失败 | 解析失败 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for source_id in sorted(source_ids):
        spec = specs.get(source_id)
        name = spec.name if spec is not None else source_id
        values = " | ".join(str(counts[kind][source_id]) for kind in _KINDS)
        lines.append(f"| {_plain(name)} | {values} |")
    if not source_ids:
        lines.append("| （无来源） | 0 | 0 | 0 | 0 | 0 |")
    lines.extend(["", "## 本周有变化的 PDF", "", _ocr_summary(deps, events), ""])
    no_change_message = "本周没有变化。" if deps.change_log is not None else "变化日志尚未接入，无法确认本周变化。"
    if not events:
        lines.extend([no_change_message, ""])

    for source_id in sorted(source_ids):
        spec = specs.get(source_id)
        name = spec.name if spec is not None else source_id
        lines.extend([f"## 来源：{_plain(name)}", ""])
        source_events = [event for event in events if event.source_id == source_id]
        if not source_events:
            lines.extend([no_change_message, ""])
        for kind in _KINDS:
            matching = [event for event in source_events if event.kind is kind]
            lines.extend([f"### {_KIND_LABELS[kind]}（{len(matching)}）", ""])
            if matching:
                lines.extend(_event_line(deps, event) for event in matching)
            else:
                lines.append("- 无记录")
            lines.append("")

    parsed_alerts = [alert if isinstance(alert, Alert) else Alert.model_validate(alert) for alert in alerts]
    lines.extend(["## 提醒", ""])
    if parsed_alerts:
        for alert in parsed_alerts:
            source = specs.get(alert.source_id)
            source_name = source.name if source is not None else alert.source_id
            lines.append(
                f"- [{alert.severity}] {_plain(source_name)}；规则：{_plain(alert.rule)}；"
                f"起始：{_plain(alert.since.isoformat())}；{_plain(alert.message)}"
            )
    else:
        lines.append("无提醒。")
    lines.extend(["", "### 本周有缺口的来源", ""])
    gaps = [alert for alert in parsed_alerts if alert.rule == "retention_gap"]
    if gaps:
        for alert in gaps:
            source = specs.get(alert.source_id)
            source_name = source.name if source is not None else alert.source_id
            lines.append(f"- {_plain(source_name)}：retention_gap；{_plain(alert.message)}")
    else:
        lines.append("无 retention_gap 提醒。")
    return "\n".join(lines).rstrip() + "\n"
