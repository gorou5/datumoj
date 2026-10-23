"""解析与索引流水线：manifest 里还没解析的快照 → 解析器 → chunk / 关键词 / 向量 / 观测值 / 目录。

只依赖契约（``datumoj.core.contracts``）；解析器用 ``discover_parsers`` 发现，存储由调用方注入。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from datumoj.core.contracts import (
    NO_PARSER,
    CatalogFilter,
    CatalogStore,
    ChangeLog,
    ChunkStore,
    ContentState,
    KeywordIndex,
    Manifest,
    ObservationStore,
    Parser,
    ParseRecord,
    ParseResult,
    ParseStatus,
    RawStore,
    Registry,
    VectorIndex,
    discover_parsers,
    max_state,
    select_parser,
)
from datumoj.changes import detect
from datumoj.core.models import (
    ChangeEvent, ChangeKind, FetchOutcome, Observation, PublishState, Snapshot, classify_failure, classify_parse_failure,
    parse_context_fingerprint,
)

_BATCH = 500
_NO_PARSER_VERSION = "-"
_LOG = logging.getLogger(__name__)


@dataclass
class PipelineDeps:
    manifest: Manifest
    raw: RawStore
    registry: Registry
    chunks: ChunkStore
    keyword: KeywordIndex
    observations: ObservationStore
    catalog: CatalogStore
    vectors: VectorIndex | None = None
    parsers: Mapping[tuple[str, str], type[Parser]] | None = None
    """None 表示用 ``discover_parsers()`` 扫描 ``datumoj.parsers``。"""
    vector_state_path: Path | None = None
    """向量暂缺状态文件；None 时状态仅保存在本 deps 实例中（便于测试和嵌入调用）。"""
    vector_state: dict[str, object] = field(default_factory=lambda: {"missing": {}, "last_error": None}, repr=False)
    vector_state_dirty: bool = field(default=False, repr=False)
    change_log: ChangeLog | None = None


@dataclass
class ParseSummary:
    parsed: list[str] = field(default_factory=list)
    """本次成功解析的 snapshot_id（包含重试成功）。"""
    newly_parsed: list[str] = field(default_factory=list)
    """本次首次解析成功的 snapshot_id。"""
    retried: list[str] = field(default_factory=list)
    """本次重试后成功的 snapshot_id。"""
    skipped: int = 0
    """本次遇到已有解析记录的快照数；失败记录会计入并重新尝试。"""
    no_parser: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    """本次解析后仍失败的 snapshot_id → 错误原因。"""
    vectors_missing: dict[str, str] = field(default_factory=dict)
    """解析成功但向量写入暂缺的 snapshot_id → 错误原因。"""


def parse_pending(deps: PipelineDeps, source_id: str | None = None) -> ParseSummary:
    """解析或复用上下文产物；每个成功快照都按持久化顺序尝试发布。"""
    parsers = deps.parsers if deps.parsers is not None else discover_parsers()
    specs = deps.registry.all()
    summary = ParseSummary()
    recovering = _recover_staged(deps, parsers, {s.id: s.kind for s in specs}, summary)
    if source_id is not None:
        specs = [s for s in specs if s.id == source_id]
        if not specs:
            raise ValueError(f"注册表里没有来源 {source_id!r}")
    for spec in specs:
        snapshots = deps.manifest.snapshots_for(spec.id)
        for snap in snapshots:
            if snap.outcome is not FetchOutcome.ok or snap.sha256 is None:
                continue
            if snap.snapshot_id in recovering:
                continue
            meta = deps.manifest.snapshot_meta(snap.snapshot_id)
            fingerprint = parse_context_fingerprint(snap, meta)
            cls = select_parser(parsers, spec.kind, snap.content_type)
            if cls is None:
                if deps.manifest.get_parse(snap.sha256, NO_PARSER, _NO_PARSER_VERSION, fingerprint) is None:
                    deps.manifest.record_parse(ParseRecord(
                        sha256=snap.sha256, parser=NO_PARSER, version=_NO_PARSER_VERSION, status=ParseStatus.no_parser,
                        context_fingerprint=fingerprint,
                    ))
                    summary.no_parser.append(snap.snapshot_id)
                else:
                    summary.skipped += 1
                continue
            previous = deps.manifest.get_parse(snap.sha256, cls.name, cls.version, fingerprint)
            if previous is not None:
                summary.skipped += 1
            is_retry = previous is not None and previous.status is ParseStatus.failed
            staged = False
            try:
                reusable = (
                    previous is not None and previous.status is ParseStatus.ok and previous.doc_keys is not None
                    and all(deps.manifest.last_writer(key, snap.sha256) == fingerprint
                            and deps.chunks.get_document(key, snap.sha256) is not None for key in previous.doc_keys)
                )
                if reusable:
                    doc_keys, events = previous.doc_keys, {}
                else:
                    doc_keys, events = _process(deps, cls(), snap)
                    # 恢复扫描必须能在发布前从解析记录定位全部产物。
                    deps.manifest.record_parse(ParseRecord(
                        sha256=snap.sha256, parser=cls.name, version=cls.version, status=ParseStatus.ok,
                        context_fingerprint=fingerprint, doc_keys=doc_keys,
                    ))
                staged = True
                needs_publish = not reusable or deps.manifest.publish_state(snap.snapshot_id) is not PublishState.published
                if needs_publish:
                    deps.manifest.record_publish_state(snap.snapshot_id, PublishState.staged)
                vector_errors = []
                for key in doc_keys:
                    vector_errors.extend(_publish_document(deps, key, snap, rewritten=not reusable, event=events.get(key)))
                if needs_publish:
                    deps.manifest.record_publish_state(snap.snapshot_id, PublishState.published)
            except Exception as exc:  # 一个坏快照不影响其他
                reason = f"{type(exc).__name__}: {exc}"
                current_written = staged and any(
                    (current := deps.chunks.get_document(key)) is not None and current.snapshot_sha256 == snap.sha256
                    for key in doc_keys
                )
                if not current_written:
                    deps.manifest.record_parse(ParseRecord(
                        sha256=snap.sha256, parser=cls.name, version=cls.version, status=ParseStatus.failed, error=reason,
                        context_fingerprint=fingerprint,
                    ))
                deps.manifest.record_publish_state(
                    snap.snapshot_id, PublishState.staged if current_written else PublishState.failed, reason,
                )
                summary.failed[snap.snapshot_id] = reason
                _record_parse_failure(deps, snap, reason)
            else:
                if not reusable:
                    summary.parsed.append(snap.snapshot_id)
                    (summary.retried if is_retry else summary.newly_parsed).append(snap.snapshot_id)
                if vector_errors:
                    summary.vectors_missing[snap.snapshot_id] = "; ".join(vector_errors)
        if deps.change_log is not None:
            try:
                _record_fetch_changes(deps, snapshots)
            except Exception:
                _LOG.warning("抓取变化检测失败：%s", spec.id, exc_info=True)
    return summary


def _recover_staged(
    deps: PipelineDeps, parsers: Mapping, kinds: Mapping[str, str], summary: ParseSummary,
) -> set[str]:
    """只按 staged 的主键与解析产物恢复，不读取快照历史或 chunk 全表。"""
    pending: set[str] = set()
    for snapshot_id in deps.manifest.snapshots_in_publish_state(PublishState.staged):
        pending.add(snapshot_id)
        try:
            snap = deps.manifest.get_snapshot(snapshot_id)
            if snap is None or snap.sha256 is None or snap.source_id not in kinds:
                raise ValueError(f"无法恢复快照：{snapshot_id}")
            fingerprint = parse_context_fingerprint(snap, deps.manifest.snapshot_meta(snapshot_id))
            cls = select_parser(parsers, kinds[snap.source_id], snap.content_type)
            record = None if cls is None else deps.manifest.get_parse(snap.sha256, cls.name, cls.version, fingerprint)
            if record is None or record.doc_keys is None:
                raise ValueError(f"缺少恢复用解析记录：{snapshot_id}")
            for key in record.doc_keys:
                current = deps.chunks.get_document(key)
                if current is None or current.snapshot_sha256 != snap.sha256:
                    continue
                chunks = deps.chunks.chunks_for(key)
                deps.keyword.delete(key)
                deps.keyword.add(chunks)
                if deps.vectors is not None and key in _load_vector_state(deps)["missing"]:
                    error = _write_vector_document(deps, key, chunks, snapshot_id)
                    if error:
                        summary.vectors_missing[snapshot_id] = error
            deps.manifest.record_publish_state(snapshot_id, PublishState.published)
            pending.remove(snapshot_id)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            summary.failed[snapshot_id] = reason
            deps.manifest.record_publish_state(snapshot_id, PublishState.staged, reason)
            _LOG.warning("发布恢复失败：%s", snapshot_id, exc_info=True)
    return pending


def _record_parse_failure(deps: PipelineDeps, snap: Snapshot, reason: str) -> None:
    if deps.change_log is None:
        return
    try:
        deps.change_log.append([ChangeEvent.make(
            source_id=snap.source_id, kind=ChangeKind.parse_failed, resource_url=snap.resource_url,
            new_sha256=snap.sha256, detected_at=datetime.now(UTC),
            summary=" ".join(f"解析失败 [{classify_parse_failure().kind.value}]；快照 {snap.snapshot_id}：{reason}".split()),
        )])
    except Exception:
        _LOG.warning("解析失败事件写入失败：%s", snap.snapshot_id, exc_info=True)


def _record_fetch_changes(deps: PipelineDeps, snapshots: list[Snapshot]) -> None:
    latest = {s.resource_url: s for s in snapshots}
    successful_by_url = defaultdict(list)
    for snap in snapshots:
        if snap.outcome is FetchOutcome.ok:
            successful_by_url[snap.resource_url].append(snap)
    urls_by_doc = defaultdict(set)
    keys_by_url = defaultdict(set)
    keys_by_sha = defaultdict(set)
    loaded = False
    for url, snap in latest.items():
        failure = classify_failure(snap.outcome, snap.http_status)
        if failure is None:
            continue
        successful = successful_by_url[url]
        old_sha = successful[-1].sha256 if successful else None
        summary = " ".join(
            f"抓取失败 [{failure.kind.value}]；快照 {snap.snapshot_id}：{snap.error or snap.outcome.value}".split()
        )
        events = [ChangeEvent.make(
            source_id=snap.source_id, kind=ChangeKind.fetch_failed, resource_url=url,
            old_sha256=old_sha, detected_at=datetime.now(UTC), summary=summary,
        )]
        if successful and snap.outcome is FetchOutcome.http_error and snap.http_status in (404, 410):
            hashes = {s.sha256 for s in successful}
            # 日志的 URL 关联也能找到没有检索块的文档；旧资料由当前块的快照引用关联。
            if not loaded:
                source_ids = sorted({s.source_id for s in snapshots})
                history = deps.change_log.list(source_ids=source_ids, limit=2**63 - 1)
                for event in history:
                    if event.doc_key and event.resource_url and event.kind in (ChangeKind.added, ChangeKind.modified):
                        urls_by_doc[event.doc_key].add(event.resource_url)
                        keys_by_url[event.resource_url].add(event.doc_key)
                for chunk in deps.chunks.iter_chunks(source_ids):
                    keys_by_sha[chunk.snapshot_sha256].add(chunk.doc_key)
                loaded = True
            keys = set(keys_by_url[url])
            for digest in hashes:
                keys.update(key for key in keys_by_sha[digest] if not urls_by_doc[key] or url in urls_by_doc[key])
            for key in sorted(keys):
                doc = deps.chunks.get_document(key)
                if doc is not None and doc.snapshot_sha256 in hashes:
                    events.append(ChangeEvent.make(
                        source_id=snap.source_id, kind=ChangeKind.removed, doc_key=key, resource_url=url,
                        old_sha256=doc.snapshot_sha256, detected_at=datetime.now(UTC),
                        summary=" ".join(f"文档已移除；HTTP {snap.http_status}；快照 {snap.snapshot_id}".split()),
                    ))
        deps.change_log.append(events)


def _process(deps: PipelineDeps, parser: Parser, snap: Snapshot) -> tuple[list[str], dict[str, ChangeEvent]]:
    assert snap.sha256 is not None
    data = deps.raw.get(snap.sha256)
    meta = deps.manifest.snapshot_meta(snap.snapshot_id)
    result = parser.parse(snap, data, meta)
    result.check_against(snap)
    events = _dispatch(deps, result, snap)
    state = ContentState.observations_loaded if result.observations else ContentState.content_fetched
    dataset_keys = {resource.dataset_key for resource in result.resources if resource.url == snap.resource_url}
    dataset_id = meta.get("dataset_id")
    if isinstance(dataset_id, str) and dataset_id:
        dataset_keys.add(f"{snap.source_id}:{dataset_id}")
    matched = False
    for dataset_key in dataset_keys:
        matched = _promote_catalog_content_state(deps, dataset_key, snap.resource_url, state) or matched
    if not matched:
        _promote_catalog_content_state_for_url(deps, snap.source_id, snap.resource_url, state)
    return [doc.doc_key for doc in result.documents], events


def _dispatch(deps: PipelineDeps, result: ParseResult, snap: Snapshot) -> dict[str, ChangeEvent]:
    events: dict[str, ChangeEvent] = {}
    if result.datasets or result.resources:
        deps.catalog.upsert(result.datasets, result.resources)
        for resource in result.resources:
            if deps.manifest.last_success(resource.url) is not None:
                _promote_catalog_content_state(
                    deps, resource.dataset_key, resource.url, ContentState.content_fetched
                )
    # 必需的观测值先写完；任何失败都不能推进文档 current。
    tables: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in result.observations:
        tables[(o.source_id, o.table_key)].append(o)
    for (src, table), obs in tables.items():
        deps.observations.write_table(src, table, obs)
    by_doc = defaultdict(list)
    for c in result.chunks:
        by_doc[c.doc_key].append(c)
    for doc in result.documents:
        chunks = by_doc[doc.doc_key]
        compare = deps.change_log is not None
        old_doc, old_text = None, ""
        if compare:
            try:
                old_doc = deps.chunks.get_document(doc.doc_key)
                old_text = "\n".join(c.text for c in deps.chunks.chunks_for(doc.doc_key))
            except Exception:
                compare = False
                _LOG.warning("读取旧文档供变化检测失败：%s", doc.doc_key, exc_info=True)
        deps.chunks.put_document(doc, chunks, publish=False)
        if compare:
            try:
                event = detect(old_doc, old_text, doc, "\n".join(c.text for c in chunks), detected_at=datetime.now(UTC))
                if event is not None:
                    events[doc.doc_key] = ChangeEvent.make(
                        **event.model_dump(exclude={"event_id", "resource_url"}), resource_url=snap.resource_url,
                    )
            except Exception:
                _LOG.warning("文档变化检测失败：%s", doc.doc_key, exc_info=True)
    return events


def _publish_document(
    deps: PipelineDeps, doc_key: str, snap: Snapshot, *, rewritten: bool = False, event: ChangeEvent | None = None,
) -> list[str]:
    order = deps.manifest.snapshot_order(snap.snapshot_id)
    if order is None:
        raise ValueError(f"没有快照顺序：{snap.snapshot_id}")
    published = deps.chunks.publish_current(doc_key, snap.sha256, order)
    # 重写同一版本槽位后，即使水位相等也要修复索引；较旧快照不能覆盖较新的索引。
    if not published:
        current = deps.chunks.get_document(doc_key)
        if not (rewritten and current is not None and current.snapshot_sha256 == snap.sha256):
            return []
    if published and event is not None:
        try:
            deps.change_log.append([event])
        except Exception:
            _LOG.warning("文档变化写入失败：%s", doc_key, exc_info=True)
    chunks = deps.chunks.chunks_for(doc_key, snap.sha256)
    if deps.vectors is not None:
        # 关键词中断或进程退出后，恢复扫描也能定位尚未写完的可选向量。
        _mark_vector_missing(deps, _load_vector_state(deps), doc_key, chunks, "发布后待写入向量", snap.snapshot_id)
    deps.keyword.delete(doc_key)
    deps.keyword.add(chunks)
    if deps.vectors is not None:
        error = _write_vector_document(deps, doc_key, chunks, snap.snapshot_id)
        if error:
            return [error]
    return []


def _promote_catalog_content_state(
    deps: PipelineDeps, dataset_key: str, resource_url: str, state: ContentState
) -> bool:
    entries = deps.catalog.resources_for(dataset_key)
    matching = [entry for entry in entries if entry.resource.url == resource_url]
    if matching:
        deps.catalog.set_content_state(resource_url, max_state([state, *(entry.state for entry in matching)]))
    return bool(matching)


def _promote_catalog_content_state_for_url(
    deps: PipelineDeps, source_id: str, resource_url: str, state: ContentState
) -> None:
    filters = CatalogFilter(source_ids=[source_id])
    for hit in deps.catalog.search("", filters=filters, k=1_000_000):
        if any(entry.resource.url == resource_url for entry in hit.resources):
            _promote_catalog_content_state(deps, hit.dataset.dataset_key, resource_url, state)


def _reconcile_catalog_content_state(deps: PipelineDeps, source_id: str | None) -> None:
    filters = CatalogFilter(source_ids=[source_id]) if source_id is not None else None
    for hit in deps.catalog.search("", filters=filters, k=1_000_000):
        for entry in hit.resources:
            resource = entry.resource
            if deps.manifest.last_success(resource.url) is not None:
                _promote_catalog_content_state(
                    deps, resource.dataset_key, resource.url, ContentState.content_fetched
                )


def reindex(deps: PipelineDeps, source_id: str | None = None, *, vectors_only: bool = False) -> int:
    """从 ChunkStore 的当前版本重建索引；vectors_only 只重试已记录的向量暂缺文档。"""
    if not vectors_only:
        _reconcile_catalog_content_state(deps, source_id)
    source_ids = None if source_id is None else [source_id]
    chunks = list(deps.chunks.iter_chunks(source_ids))
    docs: dict[str, list] = defaultdict(list)
    for chunk in chunks:
        docs[chunk.doc_key].append(chunk)

    if vectors_only:
        if deps.vectors is None:
            return 0
        state = _load_vector_state(deps)
        missing = state["missing"]
        selected = [key for key in docs if key in missing]
        return sum(_retry_vector_document(deps, key, docs[key], state) for key in selected)

    if source_id is None:
        deps.keyword.clear()
    else:
        for doc_key in docs:
            deps.keyword.delete(doc_key)
    for i in range(0, len(chunks), _BATCH):
        deps.keyword.add(chunks[i : i + _BATCH])

    if deps.vectors is not None:
        state = _load_vector_state(deps)
        if source_id is None:
            try:
                deps.vectors.clear()
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                for doc_key, doc_chunks in docs.items():
                    _mark_vector_missing(deps, state, doc_key, doc_chunks, reason, _snapshot_id(deps, doc_chunks))
            else:
                for doc_key, doc_chunks in docs.items():
                    _retry_vector_document(deps, doc_key, doc_chunks, state, delete_first=False)
        else:
            for doc_key, doc_chunks in docs.items():
                _retry_vector_document(deps, doc_key, doc_chunks, state)
    return len(chunks)


def vector_status(deps: PipelineDeps) -> dict[str, object]:
    """返回向量可用块数，或暂缺块数和最近错误。"""
    chunks = list(deps.chunks.iter_chunks())
    docs: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        docs[chunk.doc_key] += 1
    state = _load_vector_state(deps)
    missing = state["missing"]
    missing_count = sum(min(docs.get(key, 0), int(item.get("chunks", 0))) for key, item in missing.items())
    if missing_count:
        return {"state": "missing", "missing_blocks": missing_count, "error": state.get("last_error")}
    return {"state": "available", "blocks": len(chunks)}


def _load_vector_state(deps: PipelineDeps) -> dict[str, object]:
    if deps.vector_state_dirty:
        return deps.vector_state
    if deps.vector_state_path is not None and deps.vector_state_path.exists():
        try:
            data = json.loads(deps.vector_state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("missing"), dict):
                return {"missing": data["missing"], "last_error": data.get("last_error")}
        except (OSError, json.JSONDecodeError):
            pass
    missing = deps.vector_state.get("missing", {})
    return {"missing": missing if isinstance(missing, dict) else {}, "last_error": deps.vector_state.get("last_error")}


def _save_vector_state(deps: PipelineDeps, state: dict[str, object]) -> None:
    if deps.vector_state_path is None:
        deps.vector_state = state
        return
    path = deps.vector_state_path
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    deps.vector_state = state
    deps.vector_state_dirty = False


def _snapshot_id(deps: PipelineDeps, chunks: list) -> str | None:
    if not chunks:
        return None
    snapshots = deps.manifest.snapshots_by_sha256(chunks[0].snapshot_sha256)
    source_snapshots = [snapshot for snapshot in snapshots if snapshot.source_id == chunks[0].source_id]
    candidates = source_snapshots or snapshots
    return candidates[-1].snapshot_id if candidates else None


def _mark_vector_missing(
    deps: PipelineDeps,
    state: dict[str, object],
    doc_key: str,
    chunks: list,
    reason: str,
    snapshot_id: str | None,
) -> None:
    missing = state["missing"]
    missing[doc_key] = {"snapshot_id": snapshot_id, "chunks": len(chunks), "error": reason}
    state["last_error"] = reason
    try:
        _save_vector_state(deps, state)
    except OSError as exc:
        state["last_error"] = f"{reason}; 状态文件写入失败：{type(exc).__name__}: {exc}"
        deps.vector_state = state
        deps.vector_state_dirty = True


def _clear_vector_missing(deps: PipelineDeps, state: dict[str, object], doc_key: str) -> None:
    missing = state["missing"]
    changed = doc_key in missing or (not missing and state.get("last_error") is not None)
    if not changed:
        return
    missing.pop(doc_key, None)
    if not missing:
        state["last_error"] = None
    try:
        _save_vector_state(deps, state)
    except OSError:
        # 向量已经写好；保留内存状态并避免把解析误报为失败。
        deps.vector_state = state
        deps.vector_state_dirty = True


def _write_vector_document(
    deps: PipelineDeps, doc_key: str, chunks: list, snapshot_id: str
) -> str | None:
    try:
        deps.vectors.delete(doc_key)
        deps.vectors.add(chunks)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        _mark_vector_missing(deps, _load_vector_state(deps), doc_key, chunks, reason, snapshot_id)
        return reason
    _clear_vector_missing(deps, _load_vector_state(deps), doc_key)
    return None


def _retry_vector_document(
    deps: PipelineDeps,
    doc_key: str,
    chunks: list,
    state: dict[str, object],
    *,
    delete_first: bool = True,
) -> int:
    try:
        if delete_first:
            deps.vectors.delete(doc_key)
        embedded = deps.vectors.add(chunks)
    except Exception as exc:
        _mark_vector_missing(deps, state, doc_key, chunks, f"{type(exc).__name__}: {exc}", _snapshot_id(deps, chunks))
        return 0
    _clear_vector_missing(deps, state, doc_key)
    return embedded
