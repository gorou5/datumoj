"""解析与索引流水线：manifest 里还没解析的快照 → 解析器 → chunk / 关键词 / 向量 / 观测值 / 目录。

只依赖契约（``datumoj.rikolto.core.contracts``）；解析器用 ``discover_parsers`` 发现，存储由调用方注入。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from datumoj.rikolto.changes import detect
from datumoj.rikolto.core.budget import PARSE_BUDGET_META_KEY, BudgetChecker, BudgetExceeded, ParseBudget
from datumoj.rikolto.core.contracts import (
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
    SourceSpec,
    VectorIndex,
    discover_parsers,
    max_state,
    select_parser,
)
from datumoj.rikolto.core.errors import safe_reason
from datumoj.rikolto.core.models import (
    ChangeEvent,
    ChangeKind,
    Chunk,
    Dataset,
    FailureKind,
    FetchOutcome,
    Observation,
    PublishState,
    Snapshot,
    classify_failure,
    classify_parse_failure,
    parse_context_fingerprint,
)
from datumoj.rikolto.index.projections import is_projection, project, projection_fingerprint, protects_current
from datumoj.rikolto.index.vector import LanceVectorIndex

_BATCH = 500
VECTOR_FAILURE_LIMIT = 3
_NO_PARSER_VERSION = "-"
_LOG = logging.getLogger(__name__)


def _configured_vector_limit() -> int | None:
    value = os.environ.get("DATUMOJ_VECTOR_LIMIT")
    if value is None:
        return None
    try:
        limit = int(value)
    except ValueError:
        raise ValueError("DATUMOJ_VECTOR_LIMIT 必须是非负整数") from None
    if limit < 0:
        raise ValueError("DATUMOJ_VECTOR_LIMIT 必须是非负整数")
    return limit


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
    """None 表示用 ``discover_parsers()`` 扫描 ``datumoj.rikolto.parsers``。"""
    vector_state_path: Path | None = None
    """向量暂缺状态文件；None 时状态仅保存在本 deps 实例中（便于测试和嵌入调用）。"""
    vector_state: dict[str, object] = field(default_factory=lambda: {"missing": {}, "last_error": None}, repr=False)
    vector_state_dirty: bool = field(default=False, repr=False)
    change_log: ChangeLog | None = None
    vector_limit: int | None = field(default_factory=_configured_vector_limit)
    """每次流水线调用的嵌入块数上限；None 为不限。失败的尝试也占用额度。"""
    deadline: float | None = None
    """单调时钟截止时刻；快照与嵌入批次之间检查，None 为不限。"""
    clock: Callable[[], float] = time.monotonic
    _deadline_reached: bool = field(default=False, init=False, repr=False)
    _vector_commits: dict | None = field(default=None, init=False, repr=False)
    _vector_remaining: int | None = field(default=None, init=False, repr=False)
    _vectors_written: bool = field(default=False, init=False, repr=False)
    _vector_embedded: int = field(default=0, init=False, repr=False)
    _vector_pending_removed: bool = field(default=False, init=False, repr=False)
    _vector_pruned_for: frozenset[str] | None = field(default=None, init=False, repr=False)


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
    newly_failed: list[str] = field(default_factory=list)
    """首次失败的快照；重试失败仍留在 failed，但不影响来源判坏比例。"""
    cached_failures: list[str] = field(default_factory=list)
    """failed 中未重新尝试的确定性预算诊断；不计入本轮来源失败。"""
    empty_content: list[str] = field(default_factory=list)
    """正文不足的快照，不算失败。"""
    vectors_missing: dict[str, str] = field(default_factory=dict)
    """解析成功但向量写入暂缺的 snapshot_id → 错误原因。"""
    vector_remaining: int | None = None
    """本次调用剩余额度；返回给组合入口，不依赖 wiring 是否复制了 deps。"""
    stop_reason: str | None = None


def parse_pending(
    deps: PipelineDeps, source_id: str | None = None, *, deadline: float | None = None,
) -> ParseSummary:
    """解析或复用上下文产物；每个成功快照都按持久化顺序尝试发布。"""
    if deadline is not None:
        deps = replace(deps, deadline=deadline)
    try:
        return _parse_pending(deps, source_id)
    finally:
        if deps._vector_commits is not None:
            deps.vectors.abort_batch()
            deps._vector_commits = None


def _parse_pending(deps: PipelineDeps, source_id: str | None) -> ParseSummary:
    _start_vectors(deps)
    parsers = deps.parsers if deps.parsers is not None else discover_parsers()
    specs = deps.registry.all()
    specs_by_id = {spec.id: spec for spec in specs}
    budgets = {spec.id: ParseBudget.from_scope(spec.scope) for spec in specs}
    summary = ParseSummary()
    if source_id is not None:
        specs = [s for s in specs if s.id == source_id]
        if not specs:
            raise ValueError(f"注册表里没有来源 {source_id!r}")
    _begin_vector_batch(deps)
    pending_vectors = set(_load_vector_state(deps)["missing"])
    _retry_vectors(deps, source_id, summary)
    recovering = _recover_staged(deps, parsers, specs_by_id, budgets, summary)
    optional_raw = deps.manifest.raw_markers()
    attempted = set()
    for spec in specs:
        budget = budgets[spec.id]
        snapshots = deps.manifest.snapshots_for(spec.id)
        for snap in snapshots:
            if _deadline_reached(deps):
                break
            if snap.outcome is not FetchOutcome.ok or snap.sha256 is None:
                continue
            if snap.snapshot_id in recovering:
                continue
            meta = deps.manifest.snapshot_meta(snap.snapshot_id)
            cls = select_parser(parsers, spec.kind, snap.content_type)
            fingerprint = _parse_fingerprint(snap, meta, spec, budget, cls)
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
                if previous.status is ParseStatus.failed:
                    error = previous.error or ""
                    if error.startswith("ParseError: empty_content:"):
                        summary.empty_content.append(snap.snapshot_id)
                        continue
                    if error.startswith("budget_exceeded:") and error != "budget_exceeded:duration_seconds":
                        summary.failed[snap.snapshot_id] = error
                        summary.cached_failures.append(snap.snapshot_id)
                        continue
                    # 同内容、同上下文的历史快照只重试一次，避免一轮反复超时。
                    if (snap.sha256, cls.name, cls.version, fingerprint) in attempted:
                        continue
            is_retry = previous is not None and previous.status is ParseStatus.failed
            staged = False
            try:
                reusable = (
                    previous is not None and previous.status is ParseStatus.ok and previous.doc_keys is not None
                    and all((doc := deps.chunks.get_document(key, snap.sha256)) is not None
                            and (doc.metadata.get("projection") == "keyword"
                                 or deps.manifest.last_writer(key, snap.sha256) == fingerprint)
                            for key in previous.doc_keys)
                )
                if reusable:
                    doc_keys, events = previous.doc_keys, {}
                else:
                    if snap.sha256 in optional_raw and not deps.raw.exists(snap.sha256):
                        if previous is None:
                            summary.skipped += 1
                        continue
                    attempted.add((snap.sha256, cls.name, cls.version, fingerprint))
                    doc_keys, events = _process(deps, cls(), snap, budget)
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
                # Publish every document before indexing any of them. A keyword
                # failure must not leave body and projected outputs at different watermarks.
                published_keys = []
                for key in doc_keys:
                    if _publish_current(deps, key, snap, rewritten=not reusable, event=events.get(key)):
                        published_keys.append(key)
                        if (deps.vectors is not None and _source_embeds(deps, snap.source_id)
                                and not (reusable and key in pending_vectors)):
                            _mark_vector_missing(
                                deps, _load_vector_state(deps), key, deps.chunks.chunks_for(key, snap.sha256),
                                "发布后待写入向量", snap.snapshot_id, count_failure=False,
                            )
                for key in published_keys:
                    vector_errors.extend(_index_document(
                        deps, key, snap,
                        vectors_retried=reusable and key in pending_vectors,
                    ))
                if needs_publish:
                    deps.manifest.record_publish_state(snap.snapshot_id, PublishState.published)
            except Exception as exc:  # noqa: BLE001 -- 坏快照必须隔离，不能影响其他快照。
                reason = safe_reason(exc)
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
                if reason.startswith("ParseError: empty_content:"):
                    summary.empty_content.append(snap.snapshot_id)
                else:
                    summary.failed[snap.snapshot_id] = reason
                    if not is_retry:
                        summary.newly_failed.append(snap.snapshot_id)
                    _record_parse_failure(deps, snap, reason)
            else:
                if not reusable:
                    summary.parsed.append(snap.snapshot_id)
                    (summary.retried if is_retry else summary.newly_parsed).append(snap.snapshot_id)
                if vector_errors:
                    summary.vectors_missing[snap.snapshot_id] = "; ".join(vector_errors)
            if deps._vector_commits is not None and len(deps._vector_commits) >= _BATCH:
                _finish_vector_batch(deps, summary)
                _begin_vector_batch(deps)
        if deps.change_log is not None:
            try:
                _record_fetch_changes(deps, snapshots)
            except Exception as exc:  # noqa: BLE001 -- 可选抓取变化检测失败不能撤销解析结果。
                _LOG.warning("抓取变化检测失败：%s（%s）", spec.id, safe_reason(exc))
    summary.stop_reason = "deadline" if _deadline_reached(deps) else None
    summary.vector_remaining = deps._vector_remaining
    _finish_vector_batch(deps, summary)
    _maintain_vectors(deps)
    return summary


def _deadline_reached(deps: PipelineDeps) -> bool:
    if deps.deadline is not None and deps.clock() >= deps.deadline:
        deps._deadline_reached = True
    return deps._deadline_reached


def _start_vectors(deps: PipelineDeps) -> None:
    if deps.vector_limit is not None and (type(deps.vector_limit) is not int or deps.vector_limit < 0):
        raise ValueError("vector_limit 必须是非负整数或 None")
    deps._deadline_reached = False
    deps._vector_remaining = deps.vector_limit
    deps._vectors_written = False
    deps._vector_embedded = 0


def _embed_enabled(spec: SourceSpec) -> bool:
    value = spec.scope.get("embed", True)
    if type(value) is not bool:
        raise ValueError(f"来源 {spec.id!r} 的 scope.embed 必须是布尔值")
    return value


def _source_embeds(deps: PipelineDeps, source_id: str) -> bool:
    spec = deps.registry.get(source_id)
    return spec is None or _embed_enabled(spec)


def _vector_source_id(deps: PipelineDeps, chunks: list, snapshot_id: str | None) -> str | None:
    if chunks:
        return chunks[0].source_id
    snapshot = deps.manifest.get_snapshot(snapshot_id) if snapshot_id is not None else None
    return snapshot.source_id if snapshot is not None else None


def _begin_vector_batch(deps: PipelineDeps) -> None:
    # Concrete capability: dynamic Mock attributes cannot opt into deferred writes.
    if isinstance(deps.vectors, LanceVectorIndex):
        deps.vectors.begin_batch()
        deps._vector_commits = {}


def _finish_vector_batch(deps: PipelineDeps, summary: ParseSummary) -> None:
    commits = deps._vector_commits
    if commits is None:
        return
    deps._vector_commits = None
    try:
        deps.vectors.flush()
    except Exception as exc:  # noqa: BLE001 -- shared commit failure leaves every queued document retryable.
        reason = safe_reason(exc)
        for key, (chunks, pending, _, snapshot_id, embedded) in commits.items():
            _mark_vector_missing(deps, _load_vector_state(deps), key, chunks, reason, snapshot_id,
                                 reset_document=True)
            summary.vectors_missing[snapshot_id or key] = reason
            deps._vector_embedded -= embedded
        return
    # Only this batch's records can be acknowledged. Disk records from a previous
    # crash or failed batch stay pending until their own vectors are committed.
    for key, (_, pending, reason, snapshot_id, _) in commits.items():
        state = _load_vector_state(deps)
        if reason:
            _mark_vector_missing(deps, state, key, pending, reason, snapshot_id,
                                 chunk_ids=[c.chunk_id for c in pending], count_failure=False)
        else:
            _clear_vector_missing(deps, state, key)


def _maintain_vectors(deps: PipelineDeps) -> None:
    maintain = getattr(deps.vectors, "maintain", None)
    if deps._vectors_written and callable(maintain):
        try:
            maintain()
        except Exception:  # noqa: BLE001 -- 可选向量维护失败不能撤销已发布状态。
            # SDK exceptions may contain paths, configuration or source text.
            _LOG.warning("向量维护失败；已发布的状态保留")


def _retry_vectors(
    deps: PipelineDeps, source_id: str | None, summary: ParseSummary, *, exclude: set[str] | None = None,
) -> int:
    """Retry pending documents only; return successfully embedded blocks, without parsing."""
    missing = _load_vector_state(deps)["missing"]
    if deps.vectors is None:
        return 0
    owns_batch = deps._vector_commits is None
    if owns_batch:
        _begin_vector_batch(deps)
    before = deps._vector_embedded
    try:
        staged = set(deps.manifest.snapshots_in_publish_state(PublishState.staged))
        for key, item in list(missing.items()):
            if deps._vector_remaining == 0 or _deadline_reached(deps):
                break
            if (item.get("failures", 0) >= VECTOR_FAILURE_LIMIT or item.get("snapshot_id") in staged
                    or (exclude is not None and key in exclude)):
                continue
            chunks = deps.chunks.chunks_for(key)
            if source_id is not None and chunks and chunks[0].source_id != source_id:
                continue
            error = _write_vector_document(deps, key, chunks, _snapshot_id(deps, chunks), reset=False)
            if error:
                summary.vectors_missing[_snapshot_id(deps, chunks) or key] = error
            if deps._vector_commits is not None and len(deps._vector_commits) >= _BATCH:
                _finish_vector_batch(deps, summary)
                _begin_vector_batch(deps)
    except BaseException:
        if owns_batch and deps._vector_commits is not None:
            deps.vectors.abort_batch()
            deps._vector_commits = None
        raise
    if owns_batch:
        _finish_vector_batch(deps, summary)
    return deps._vector_embedded - before


def _recover_staged(
    deps: PipelineDeps,
    parsers: Mapping,
    specs: Mapping[str, SourceSpec],
    budgets: Mapping[str, ParseBudget],
    summary: ParseSummary,
) -> set[str]:
    """只按 staged 的主键与解析产物恢复，不读取快照历史或 chunk 全表。"""
    pending: set[str] = set()
    _load_vector_state(deps)
    for snapshot_id in deps.manifest.snapshots_in_publish_state(PublishState.staged):
        pending.add(snapshot_id)
        try:
            snap = deps.manifest.get_snapshot(snapshot_id)
            if snap is None or snap.sha256 is None or snap.source_id not in specs:
                raise ValueError(f"无法恢复快照：{snapshot_id}")
            spec = specs[snap.source_id]
            meta = deps.manifest.snapshot_meta(snapshot_id)
            cls = select_parser(parsers, spec.kind, snap.content_type)
            fingerprint = _parse_fingerprint(snap, meta, spec, budgets[snap.source_id], cls)
            record = None if cls is None else deps.manifest.get_parse(snap.sha256, cls.name, cls.version, fingerprint)
            if record is None or record.doc_keys is None:
                raise ValueError(f"缺少恢复用解析记录：{snapshot_id}")
            recovered = {}
            projected = None
            for key in record.doc_keys:
                current = deps.chunks.get_document(key)
                if current is None or current.snapshot_sha256 != snap.sha256:
                    continue
                if current.metadata.get("projection") == "keyword":
                    # Structured-only outputs can be reconstructed without reading
                    # a second copy of every serialized row from the block store.
                    if projected is None:
                        datasets, observations = _projection_inputs(deps, snap.source_id)
                        projected = {doc.doc_key: chunks for doc, chunks in project(
                            [d for d in datasets if d.snapshot_sha256 == snap.sha256],
                            [o for o in observations if o.snapshot_sha256 == snap.sha256],
                        )}
                    chunks = projected.get(key, [])
                    if projection_fingerprint(chunks) != current.metadata.get("projection_fingerprint"):
                        chunks = deps.chunks.chunks_for(key)
                else:
                    chunks = deps.chunks.chunks_for(key)
                deps.keyword.delete(key)
                recovered[key] = chunks
            if recovered:
                deps.keyword.add([chunk for chunks in recovered.values() for chunk in chunks])
            for key, chunks in recovered.items():
                missing = _load_vector_state(deps)["missing"].get(key)
                if (_embed_enabled(spec) and deps.vectors is not None and missing is not None
                        and missing.get("failures", 0) < VECTOR_FAILURE_LIMIT
                        and snapshot_id not in summary.vectors_missing and deps._vector_remaining != 0):
                    error = _write_vector_document(deps, key, chunks, snapshot_id, reset=False)
                    if error:
                        summary.vectors_missing[snapshot_id] = error
            deps.manifest.record_publish_state(snapshot_id, PublishState.published)
            pending.remove(snapshot_id)
        except Exception as exc:  # noqa: BLE001 -- 单快照发布恢复失败不能阻止其他快照恢复。
            reason = safe_reason(exc)
            summary.failed[snapshot_id] = reason
            deps.manifest.record_publish_state(snapshot_id, PublishState.staged, reason)
            _LOG.warning("发布恢复失败：%s（%s）", snapshot_id, safe_reason(exc))
    return pending


def _parse_fingerprint(
    snap: Snapshot, meta: Mapping, spec: SourceSpec, budget: ParseBudget, parser: type[Parser] | None,
) -> str:
    fingerprint_meta = meta
    budget_aware = parser is not None and parser.name in {"pdf", "gov-web-html-page"}
    if "parse_budget" in spec.scope or budget_aware:
        fingerprint_meta = {**meta, PARSE_BUDGET_META_KEY: budget.as_dict()}
    return parse_context_fingerprint(snap, fingerprint_meta)


def _record_parse_failure(deps: PipelineDeps, snap: Snapshot, reason: str) -> None:
    if deps.change_log is None:
        return
    try:
        kind = FailureKind.policy if reason.startswith("budget_exceeded:") else classify_parse_failure().kind
        deps.change_log.append([ChangeEvent.make(
            source_id=snap.source_id, kind=ChangeKind.parse_failed, resource_url=snap.resource_url,
            new_sha256=snap.sha256, detected_at=datetime.now(UTC),
            summary=" ".join(f"解析失败 [{kind.value}]；快照 {snap.snapshot_id}：{reason}".split()),
        )])
    except Exception as exc:  # noqa: BLE001 -- 失败事件写入失败不能覆盖原解析错误。
        _LOG.warning("解析失败事件写入失败：%s（%s）", snap.snapshot_id, safe_reason(exc))


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


def _process(
    deps: PipelineDeps, parser: Parser, snap: Snapshot, budget: ParseBudget | None = None,
) -> tuple[list[str], dict[str, ChangeEvent]]:
    assert snap.sha256 is not None
    budget = budget or ParseBudget()
    checker = BudgetChecker(budget)
    data = deps.raw.get(snap.sha256)
    meta = deps.manifest.snapshot_meta(snap.snapshot_id)
    parser_meta = meta
    if parser.name in {"pdf", "gov-web-html-page"}:
        parser_meta = {**meta, PARSE_BUDGET_META_KEY: budget.as_dict()}
    result = parser.parse(snap, data, parser_meta)
    checker.check_time()
    output_text_chars = 0
    for chunk_number, chunk in enumerate(result.chunks, 1):
        checker.check_time()
        if chunk_number > budget.max_output_chunks:
            raise BudgetExceeded("output_chunks")
        output_text_chars += len(chunk.text)
        checker.check_text_chars(output_text_chars)
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
        _write_observation_table(deps, src, table, obs, snap)
    # Add synthetic documents to the same staged/published recovery path.
    documents = {doc.doc_key: doc for doc in result.documents}
    for projected_doc, projected_chunks in project(result.datasets, result.observations):
        key = projected_doc.doc_key
        existing_doc = deps.chunks.get_document(key, projected_doc.snapshot_sha256)
        if key not in documents:
            doc = existing_doc or projected_doc
            result.documents.append(doc)
            documents[key] = doc
            result.chunks.extend(deps.chunks.chunks_for(key, doc.snapshot_sha256))
        ids = {chunk.chunk_id for chunk in result.chunks if chunk.doc_key == key}
        result.chunks.extend(chunk for chunk in projected_chunks if chunk.chunk_id not in ids)
    by_doc = defaultdict(list)
    for c in result.chunks:
        by_doc[c.doc_key].append(c)
    for doc in result.documents:
        chunks = by_doc[doc.doc_key]
        compare = deps.change_log is not None and doc.metadata.get("projection") != "keyword"
        old_doc, old_text = None, ""
        if compare:
            try:
                old_doc = deps.chunks.get_document(doc.doc_key)
                old_text = "\n".join(c.text for c in deps.chunks.chunks_for(doc.doc_key) if not is_projection(c))
            except Exception as exc:  # noqa: BLE001 -- 旧文档读取失败时按 manifest 顺序保护已发布内容。
                compare = False
                _LOG.warning("读取旧文档供变化检测失败：%s（%s）", doc.doc_key, safe_reason(exc))
        if doc.metadata.get("projection") == "keyword":
            doc = doc.model_copy(update={"metadata": {
                **doc.metadata, "projection_fingerprint": projection_fingerprint(chunks),
            }})
        deps.chunks.put_document(doc, chunks, publish=False)
        if compare:
            try:
                text = "\n".join(c.text for c in chunks if not is_projection(c))
                event = detect(old_doc, old_text, doc, text, detected_at=datetime.now(UTC))
                if event is not None:
                    events[doc.doc_key] = ChangeEvent.make(
                        **event.model_dump(exclude={"event_id", "resource_url"}), resource_url=snap.resource_url,
                    )
            except Exception as exc:  # noqa: BLE001 -- 可选文档变化检测失败不能撤销发布。
                _LOG.warning("文档变化检测失败：%s（%s）", doc.doc_key, safe_reason(exc))
    return events


def _write_observation_table(
    deps: PipelineDeps, source_id: str, table_key: str, observations: list[Observation], snap: Snapshot,
) -> None:
    order = deps.manifest.snapshot_order(snap.snapshot_id)
    if order is None:
        raise ValueError(f"没有快照顺序：{snap.snapshot_id}")
    if deps.observations.table_order(source_id, table_key) is None:
        # 只通过查询契约读升级前的行，不依赖 Parquet 实现或另建迁移文件。
        source = source_id.replace("'", "''")
        key = table_key.replace("'", "''")
        where = f"source_id = '{source}' AND table_key = '{key}'"
        current: list[Observation] = []
        lookup_failed = False
        try:
            count = deps.observations.query_readonly(f"SELECT count(*) FROM observations WHERE {where}").rows[0][0]
            if count:
                current = deps.observations.query_readonly(
                    f"SELECT source_id, table_key, obs_id FROM observations WHERE {where}", max_rows=count,
                ).provenance
                lookup_failed = len(current) != count
        except Exception as exc:  # noqa: BLE001 -- 旧观测读取失败时按 manifest 顺序避免数据回退。
            lookup_failed = True
            _LOG.warning("读取旧观测值表失败；按 manifest 顺序保护写入：%s/%s（%s）", source_id, table_key, safe_reason(exc))
        if current or lookup_failed:
            current_orders = []
            for sha in {o.snapshot_sha256 for o in current}:
                for candidate in deps.manifest.snapshots_by_sha256(sha):
                    if candidate.source_id != source_id:
                        continue
                    candidate_order = deps.manifest.snapshot_order(candidate.snapshot_id)
                    if candidate_order is not None:
                        current_orders.append(candidate_order)
            if current_orders and not lookup_failed:
                # 先为原表补水位；旧重试被挡下时也要持久化迁移结果。
                deps.observations.write_table(source_id, table_key, current, order=max(current_orders))
            else:
                known_orders = [order]
                for candidate in deps.manifest.snapshots_for(snap.source_id):
                    if candidate.resource_url == snap.resource_url:
                        candidate_order = deps.manifest.snapshot_order(candidate.snapshot_id)
                        if candidate_order is not None:
                            known_orders.append(candidate_order)
                if order < max(known_orders):
                    _LOG.warning("观测值表快照缺少 manifest 记录，为避免回退跳过写入：%s/%s", source_id, table_key)
                    return
    deps.observations.write_table(source_id, table_key, observations, order=order)


def _publish_current(
    deps: PipelineDeps, doc_key: str, snap: Snapshot, *, rewritten: bool = False, event: ChangeEvent | None = None,
) -> bool:
    order = deps.manifest.snapshot_order(snap.snapshot_id)
    if order is None:
        raise ValueError(f"没有快照顺序：{snap.snapshot_id}")
    current_order = deps.chunks.current_order(doc_key)
    current_lookup_failed = False
    current = None
    if current_order is None:
        try:
            current = deps.chunks.get_document(doc_key)
        except Exception as exc:  # noqa: BLE001 -- 当前文档读取失败时按 manifest 顺序避免发布回退。
            current_lookup_failed = True
            _LOG.warning("读取当前文档失败；按 manifest 顺序保护发布：%s（%s）", doc_key, safe_reason(exc))
    if current_order is None and (current is not None or current_lookup_failed):
        current_orders = []
        if current is not None:
            for candidate in deps.manifest.snapshots_by_sha256(current.snapshot_sha256):
                if candidate.source_id != snap.source_id or candidate.resource_url != snap.resource_url:
                    continue
                candidate_order = deps.manifest.snapshot_order(candidate.snapshot_id)
                if candidate_order is not None:
                    current_orders.append(candidate_order)
        if current_orders:
            deps.chunks.publish_current(doc_key, current.snapshot_sha256, max(current_orders))
        else:
            known_orders = [order]
            for candidate in deps.manifest.snapshots_for(snap.source_id):
                if candidate.resource_url != snap.resource_url:
                    continue
                candidate_order = deps.manifest.snapshot_order(candidate.snapshot_id)
                if candidate_order is not None:
                    known_orders.append(candidate_order)
            if order < max(known_orders):
                _LOG.warning("当前文档快照缺少 manifest 记录，为避免回退跳过发布：%s", doc_key)
                return False
    candidate = deps.chunks.chunks_for(doc_key, snap.sha256)
    if protects_current(deps.chunks.chunks_for(doc_key), candidate):
        return False
    published = deps.chunks.publish_current(doc_key, snap.sha256, order)
    # 重写同一版本槽位后，即使水位相等也要修复索引；较旧快照不能覆盖较新的索引。
    if not published:
        current = deps.chunks.get_document(doc_key)
        if not (rewritten and current is not None and current.snapshot_sha256 == snap.sha256):
            return False
    if published and event is not None:
        try:
            deps.change_log.append([event])
        except Exception as exc:  # noqa: BLE001 -- 可选变化日志写入失败不能撤销发布。
            _LOG.warning("文档变化写入失败：%s（%s）", doc_key, safe_reason(exc))
    return True


def _index_document(
    deps: PipelineDeps, doc_key: str, snap: Snapshot, *, vectors_retried: bool = False,
) -> list[str]:
    chunks = deps.chunks.chunks_for(doc_key, snap.sha256)
    write_vectors = deps.vectors is not None and not vectors_retried and _source_embeds(deps, snap.source_id)
    deps.keyword.delete(doc_key)
    deps.keyword.add(chunks)
    if write_vectors:
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


def _projection_inputs(deps: PipelineDeps, source_id: str | None) -> tuple[list[Dataset], list[Observation]]:
    filters = CatalogFilter(source_ids=[source_id]) if source_id is not None else None
    datasets = [hit.dataset for hit in deps.catalog.search("", filters=filters, k=1_000_000)]
    observations = []
    for table in deps.observations.describe().tables:
        if source_id is not None and table.source_id != source_id:
            continue
        source = table.source_id.replace("'", "''")
        key = table.table_key.replace("'", "''")
        observations.extend(deps.observations.query_readonly(
            f"SELECT * FROM observations WHERE source_id = '{source}' AND table_key = '{key}'",
            max_rows=max(1, table.rows),
        ).provenance)
    return datasets, observations


def backfill_projections(deps: PipelineDeps, source_id: str | None = None) -> int:
    """Restore projection blocks for reindex, without opening raw or invoking parsers."""
    datasets, observations = _projection_inputs(deps, source_id)
    count = 0
    projections = project(datasets, observations)
    # Rows select the current snapshot before metadata from another snapshot.
    projections.sort(key=lambda item: all("#projection:entry:" in chunk.chunk_id for chunk in item[1]))
    for doc, projected in projections:
        snapshots = [snap for snap in deps.manifest.snapshots_by_sha256(doc.snapshot_sha256)
                     if snap.source_id == doc.source_id and snap.outcome is FetchOutcome.ok]
        if not snapshots:
            raise ValueError(f"投影缺少来源快照：{doc.doc_key}")
        snap = max(snapshots, key=lambda snap: deps.manifest.snapshot_order(snap.snapshot_id))
        existing_doc = deps.chunks.get_document(doc.doc_key, doc.snapshot_sha256)
        existing = deps.chunks.chunks_for(doc.doc_key, doc.snapshot_sha256)
        merged = {chunk.chunk_id: chunk for chunk in existing}
        merged.update((chunk.chunk_id, chunk) for chunk in projected)
        combined = list(merged.values())
        if combined != existing:
            document = existing_doc or doc
            if document.metadata.get("projection") == "keyword":
                document = document.model_copy(update={"metadata": {
                    **document.metadata, "projection_fingerprint": projection_fingerprint(combined),
                }})
            deps.chunks.put_document(document, combined, publish=False)
            existing_ids = {chunk.chunk_id for chunk in existing}
            count += sum(chunk.chunk_id not in existing_ids for chunk in projected)
        _publish_current(deps, doc.doc_key, snap, rewritten=True)
    return count


def reindex(deps: PipelineDeps, source_id: str | None = None, *, vectors_only: bool = False) -> int:
    """从 ChunkStore 流式重建索引；vectors_only 只重试已记录的向量暂缺文档。"""
    _start_vectors(deps)
    if not vectors_only:
        backfill_projections(deps, source_id)
    _load_vector_state(deps)
    preserve_vectors = any(not _embed_enabled(spec) for spec in deps.registry.all())
    if deps.vectors is not None and deps.vector_limit is not None:
        return _limited_reindex(deps, source_id, vectors_only=vectors_only)
    if not vectors_only:
        _reconcile_catalog_content_state(deps, source_id)
    source_ids = None if source_id is None else [source_id]
    if vectors_only and deps.vectors is None:
        return 0

    state = _load_vector_state(deps) if deps.vectors is not None else None
    missing = state["missing"] if state is not None else {}
    selected = set(missing)
    if vectors_only and source_id is not None:
        selected = {chunk.doc_key for chunk in deps.chunks.iter_chunks(source_ids) if chunk.doc_key in missing}
    if vectors_only and not selected:
        return 0

    begin_generation = getattr(deps.vectors, "begin_generation", None) if deps.vectors is not None else None
    finish_generation = getattr(deps.vectors, "finish_generation", None) if deps.vectors is not None else None
    abort_generation = getattr(deps.vectors, "abort_generation", None) if deps.vectors is not None else None
    staged = all(callable(method) for method in (begin_generation, finish_generation, abort_generation))
    if preserve_vectors and not callable(getattr(deps.vectors, "count_rows", None)):
        staged = False
    generation_started = False
    if not vectors_only and source_id is None:
        deps.keyword.clear()

    vector_clear_failed = False
    if staged:
        begin_generation(copy_current=vectors_only or source_id is not None or preserve_vectors)
        generation_started = True
    elif deps.vectors is not None and not vectors_only and source_id is None and not preserve_vectors:
        try:
            deps.vectors.clear()
        except Exception as exc:  # noqa: BLE001 -- 向量清理失败须标记待重试并保留关键词索引。
            vector_clear_failed = True
            reason = safe_reason(exc)
            for chunk in deps.chunks.iter_chunks(source_ids):
                doc_chunks = deps.chunks.chunks_for(chunk.doc_key)
                _mark_vector_missing(deps, state, chunk.doc_key, doc_chunks or [chunk], reason,
                                     _snapshot_id(deps, doc_chunks or [chunk]))

    expected_count = 0
    keyword_seen: set[str] = set()
    vector_deleted: set[str] = set(selected if vectors_only else ())
    vector_seen: set[str] = set()
    vector_failed: set[str] = set()
    embedded = 0
    processed = 0

    try:
        if staged and (source_id is not None or vectors_only):
            expected_count = sum(1 for chunk in deps.chunks.iter_chunks() if not is_projection(chunk))
        if staged and vectors_only:
            for doc_key in selected:
                deps.vectors.delete(doc_key)
        elif deps.vectors is not None and vectors_only and not staged:
            for doc_key in selected:
                try:
                    deps.vectors.delete(doc_key)
                except Exception as exc:  # noqa: BLE001 -- 向量 SDK 单文档失败须记录并隔离。
                    doc_chunks = deps.chunks.chunks_for(doc_key)
                    _mark_vector_missing(
                        deps, state, doc_key, doc_chunks, safe_reason(exc),
                        _snapshot_id(deps, doc_chunks),
                    )
                    vector_failed.add(doc_key)

        for batch in _chunk_batches(deps.chunks.iter_chunks(source_ids), _BATCH):
            processed += len(batch)
            if staged and source_id is None and not vectors_only:
                expected_count += sum(not is_projection(chunk) for chunk in batch)
            if not vectors_only:
                for chunk in batch:
                    if source_id is not None and chunk.doc_key not in keyword_seen:
                        deps.keyword.delete(chunk.doc_key)
                        keyword_seen.add(chunk.doc_key)
                deps.keyword.add(batch)

            if deps.vectors is None or vector_clear_failed:
                continue
            vector_batch = [chunk for chunk in batch if not is_projection(chunk) and _source_embeds(deps, chunk.source_id)
                            and (not vectors_only or chunk.doc_key in selected)]
            if not vector_batch:
                continue
            vector_seen.update(chunk.doc_key for chunk in vector_batch)
            try:
                if (source_id is not None or preserve_vectors) and not vectors_only:
                    for chunk in vector_batch:
                        if chunk.doc_key not in vector_deleted:
                            deps.vectors.delete(chunk.doc_key)
                            vector_deleted.add(chunk.doc_key)
                deps._vectors_written = True
                embedded += deps.vectors.add(vector_batch)
            except Exception as exc:  # 候选向量写入失败须保留待补列表，staged 模式继续抛出。
                reason = safe_reason(exc)
                failed_docs = {chunk.doc_key for chunk in vector_batch}
                for doc_key in failed_docs:
                    doc_chunks = deps.chunks.chunks_for(doc_key)
                    doc_chunks = doc_chunks or [chunk for chunk in vector_batch if chunk.doc_key == doc_key]
                    _mark_vector_missing(deps, state, doc_key, doc_chunks, reason, _snapshot_id(deps, doc_chunks))
                vector_failed.update(failed_docs)
                if staged:
                    raise

        if staged:
            finish_generation(deps.vectors.count_rows() if preserve_vectors else expected_count)
            generation_started = False
        if state is not None:
            successful = (selected if vectors_only else vector_seen) - vector_failed
            _clear_vector_missing_many(deps, state, successful)
    except Exception:  # 候选代失败须先回滚清理，再原样抛出，不能发布半成品。
        if generation_started:
            abort_generation()
        raise
    _maintain_vectors(deps)
    return embedded if vectors_only else processed


def _limited_reindex(deps: PipelineDeps, source_id: str | None, *, vectors_only: bool) -> int:
    """部分重建仍在候选代完成；待补列表逐块记录，避免下次重复嵌入。"""
    if not vectors_only:
        _reconcile_catalog_content_state(deps, source_id)
    state = _load_vector_state(deps)
    missing = state["missing"]
    source_ids = None if source_id is None else [source_id]
    docs = dict.fromkeys(chunk.doc_key for chunk in deps.chunks.iter_chunks(source_ids))
    if vectors_only:
        docs = {key: None for key in docs if key in missing}
    if not docs:
        return 0
    begin = getattr(deps.vectors, "begin_generation", None)
    staged = all(callable(getattr(deps.vectors, name, None)) for name in (
        "begin_generation", "finish_generation", "abort_generation", "count_rows",
    ))
    if staged:
        begin(copy_current=vectors_only or source_id is not None
              or any(not _embed_enabled(spec) for spec in deps.registry.all()))
    processed = embedded = 0
    try:
        if not vectors_only and source_id is None:
            deps.keyword.clear()
        # Missing documents get the allowance before other documents.
        for key in sorted(docs, key=lambda key: key not in missing):
            chunks = deps.chunks.chunks_for(key)
            processed += len(chunks)
            if not vectors_only:
                if source_id is not None:
                    deps.keyword.delete(key)
                deps.keyword.add(chunks)
            before = deps._vector_remaining
            error = _write_vector_document(deps, key, chunks, _snapshot_id(deps, chunks),
                                           reset=not vectors_only or key not in missing)
            embedded += before - deps._vector_remaining
            if error and error != "vector_limit":
                raise RuntimeError("候选向量代写入失败")
        if staged:
            deps.vectors.finish_generation(deps.vectors.count_rows())
    except Exception:  # 候选代失败须先回滚清理，再原样抛出，不能发布半成品。
        if staged:
            deps.vectors.abort_generation()
        # A candidate may have consumed the pending list before being aborted.
        for key in docs:
            chunks = deps.chunks.chunks_for(key)
            _mark_vector_missing(deps, _load_vector_state(deps), key, chunks,
                                 "重建中断，待重试", _snapshot_id(deps, chunks), count_failure=False)
        raise
    _maintain_vectors(deps)
    return embedded if vectors_only else processed


def _chunk_batches(chunks: Iterable[Chunk], size: int) -> Iterable[list[Chunk]]:
    batch = []
    for chunk in chunks:
        batch.append(chunk)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def _clear_vector_missing_many(deps: PipelineDeps, state: dict[str, object], doc_keys: set[str]) -> None:
    missing = state["missing"]
    changed = False
    for doc_key in doc_keys:
        if doc_key in missing:
            missing.pop(doc_key)
            changed = True
    if not missing and state.get("last_error") is not None:
        state["last_error"] = None
        changed = True
    if not changed:
        return
    try:
        _save_vector_state(deps, state)
    except OSError:
        deps.vector_state = state
        deps.vector_state_dirty = True


def vector_status(deps: PipelineDeps) -> dict[str, object]:
    """返回重试状态；blocks 沿用检索块总数，missing_blocks 只数可嵌入块。"""
    chunks = list(deps.chunks.iter_chunks())
    docs: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        if not is_projection(chunk):
            docs[chunk.doc_key] += 1
    state = _load_vector_state(deps)
    missing = state["missing"]
    missing_count = sum(min(docs.get(key, 0), int(item.get("chunks", 0))) for key, item in missing.items())
    if missing_count:
        return {"state": "missing", "missing_blocks": missing_count, "error": state.get("last_error")}
    return {"state": "available", "blocks": len(chunks)}


def _load_vector_state(deps: PipelineDeps) -> dict[str, object]:
    if deps.vector_state_dirty:
        state = deps.vector_state
    elif deps.vector_state_path is not None and deps.vector_state_path.exists():
        try:
            data = json.loads(deps.vector_state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("missing"), dict):
                state = {"missing": data["missing"], "last_error": data.get("last_error")}
            else:
                state = deps.vector_state
        except (OSError, json.JSONDecodeError):
            state = deps.vector_state
    else:
        state = deps.vector_state
    missing = state.get("missing", {})
    missing = missing if isinstance(missing, dict) else {}
    for item in missing.values():
        failures = item.setdefault("failures", 0)
        if type(failures) is not int or failures < 0:
            raise ValueError("向量重试计数无效")
    state = {"missing": missing, "last_error": state.get("last_error")}
    disabled = frozenset(spec.id for spec in deps.registry.all() if not _embed_enabled(spec))
    removed = set()
    # Disabled sources never add entries, so one prune per disabled set suffices;
    # rescanning on every load costs N chunks_for queries per vector write.
    if deps._vector_pruned_for != disabled:
        for key, item in missing.items() if disabled else ():
            chunks = deps.chunks.chunks_for(key)
            if _vector_source_id(deps, chunks, item.get("snapshot_id")) in disabled:
                removed.add(key)
        if removed:
            _clear_vector_missing_many(deps, state, removed)
            deps._vector_pending_removed = True
        # Recorded only after a full scan, so a failed scan is retried on the next load.
        deps._vector_pruned_for = disabled
    return state


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
    *, chunk_ids: list[str] | None = None, count_failure: bool = True, reset_document: bool = False,
) -> None:
    chunks = [chunk for chunk in chunks if not is_projection(chunk)]
    source_id = _vector_source_id(deps, chunks, snapshot_id)
    if not chunks or (source_id is not None and not _source_embeds(deps, source_id)):
        return
    missing = state["missing"]
    previous = missing.get(doc_key, {})
    failures = previous.get("failures", 0)
    failures += int(count_failure and reason != "vector_limit")
    missing[doc_key] = {"snapshot_id": snapshot_id, "chunks": len(chunks), "error": reason, "failures": failures}
    if reset_document:
        missing[doc_key]['reset_document'] = True
    if chunk_ids is not None:
        missing[doc_key]["chunk_ids"] = chunk_ids
    state["last_error"] = reason
    try:
        _save_vector_state(deps, state)
    except OSError as exc:
        state["last_error"] = f"{reason}; 状态文件写入失败：{safe_reason(exc)}"
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
    deps: PipelineDeps, doc_key: str, chunks: list, snapshot_id: str | None, *, reset: bool = True,
) -> str | None:
    chunks = [chunk for chunk in chunks if not is_projection(chunk)]
    state = _load_vector_state(deps)
    if not chunks:
        _clear_vector_missing(deps, state, doc_key)
        return None
    source_id = _vector_source_id(deps, chunks, snapshot_id)
    if source_id is not None and not _source_embeds(deps, source_id):
        return None
    previous = state["missing"].get(doc_key, {})
    reset = reset or bool(previous.get("reset_document"))
    ids = set(previous["chunk_ids"]) if not reset and "chunk_ids" in previous else None
    pending = chunks if ids is None else [c for c in chunks if c.chunk_id in ids]
    reason = None
    deferred = deps._vector_commits is not None
    before = deps._vector_embedded
    if deferred:
        # Persist full retry intent before any deferred write. A process exit
        # cannot leave only the unembedded suffix while losing queued vectors.
        _mark_vector_missing(deps, state, doc_key, chunks, '提交前待写入向量', snapshot_id,
                             count_failure=False, reset_document=True)
    try:
        if reset or ids is None:
            deps.vectors.delete(doc_key)
            deps._vectors_written = True
        while pending:
            if _deadline_reached(deps):
                reason = "deadline"
                break
            if deps._vector_remaining == 0:
                reason = "vector_limit"
                break
            size = min(_BATCH, deps._vector_remaining) if deps._vector_remaining is not None else _BATCH
            batch = pending[:size]
            # Failed attempts also consume the allowance.
            if deps._vector_remaining is not None:
                deps._vector_remaining -= len(batch)
            deps._vectors_written = True
            deps._vector_embedded += deps.vectors.add(batch)
            pending = pending[len(batch):]
    except Exception as exc:  # noqa: BLE001 -- 向量 SDK 单文档失败须保留待补状态。
        reason = safe_reason(exc)
    if deferred:
        prior = deps._vector_commits.get(doc_key)
        embedded = deps._vector_embedded - before + (prior[4] if prior else 0)
        deps._vector_commits[doc_key] = (chunks, pending, reason, snapshot_id, embedded)
        if reason:
            # Count embed failures once, retain the full document until flush.
            _mark_vector_missing(deps, state, doc_key, chunks, reason, snapshot_id,
                                 count_failure=reason != 'deadline', reset_document=True)
        return reason
    if reason:
        _mark_vector_missing(deps, state, doc_key, pending, reason, snapshot_id,
                             chunk_ids=[c.chunk_id for c in pending], count_failure=reason != "deadline")
        return reason
    _clear_vector_missing(deps, state, doc_key)
    return None
