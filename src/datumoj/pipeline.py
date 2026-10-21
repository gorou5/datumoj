"""解析与索引流水线：manifest 里还没解析的快照 → 解析器 → chunk / 关键词 / 向量 / 观测值 / 目录。

只依赖契约（``datumoj.core.contracts``）；解析器用 ``discover_parsers`` 发现，存储由调用方注入。
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from datumoj.core.contracts import (
    NO_PARSER,
    CatalogStore,
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
    select_parser,
)
from datumoj.core.models import FetchOutcome, Observation, Snapshot

_BATCH = 500
_NO_PARSER_VERSION = "-"


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
    """解析还没处理的快照。按抓取时间顺序处理，所以同一文档最后写入的就是当前版本。"""
    parsers = deps.parsers if deps.parsers is not None else discover_parsers()
    specs = deps.registry.all()
    if source_id is not None:
        specs = [s for s in specs if s.id == source_id]
        if not specs:
            raise ValueError(f"注册表里没有来源 {source_id!r}")
    summary = ParseSummary()
    for spec in specs:
        for snap in deps.manifest.snapshots_for(spec.id):
            if snap.outcome is not FetchOutcome.ok or snap.sha256 is None:
                continue
            cls = select_parser(parsers, spec.kind, snap.content_type)
            if cls is None:
                if deps.manifest.get_parse(snap.sha256, NO_PARSER, _NO_PARSER_VERSION) is None:
                    deps.manifest.record_parse(ParseRecord(
                        sha256=snap.sha256, parser=NO_PARSER, version=_NO_PARSER_VERSION, status=ParseStatus.no_parser,
                    ))
                    summary.no_parser.append(snap.snapshot_id)
                else:
                    summary.skipped += 1
                continue
            previous = deps.manifest.get_parse(snap.sha256, cls.name, cls.version)
            if previous is not None:
                summary.skipped += 1
                if previous.status is not ParseStatus.failed:
                    continue
            is_retry = previous is not None and previous.status is ParseStatus.failed
            try:
                vector_errors = _process(deps, cls(), snap)
            except Exception as exc:  # 一个坏快照不影响其他
                reason = f"{type(exc).__name__}: {exc}"
                deps.manifest.record_parse(ParseRecord(
                    sha256=snap.sha256, parser=cls.name, version=cls.version, status=ParseStatus.failed, error=reason,
                ))
                summary.failed[snap.snapshot_id] = reason
            else:
                deps.manifest.record_parse(
                    ParseRecord(sha256=snap.sha256, parser=cls.name, version=cls.version, status=ParseStatus.ok)
                )
                summary.parsed.append(snap.snapshot_id)
                (summary.retried if is_retry else summary.newly_parsed).append(snap.snapshot_id)
                if vector_errors:
                    summary.vectors_missing[snap.snapshot_id] = "; ".join(vector_errors)
    return summary


def _process(deps: PipelineDeps, parser: Parser, snap: Snapshot) -> list[str]:
    assert snap.sha256 is not None
    data = deps.raw.get(snap.sha256)
    result = parser.parse(snap, data, deps.manifest.snapshot_meta(snap.snapshot_id))
    result.check_against(snap)
    vector_errors = _dispatch(deps, result, snap.snapshot_id)
    if result.observations:
        state = ContentState.observations_loaded
    elif result.chunks:
        state = ContentState.content_fetched
    else:
        return vector_errors
    deps.catalog.set_content_state(snap.resource_url, state)
    return vector_errors


def _dispatch(deps: PipelineDeps, result: ParseResult, snapshot_id: str) -> list[str]:
    vector_errors: list[str] = []
    if result.datasets or result.resources:
        deps.catalog.upsert(result.datasets, result.resources)
    by_doc = defaultdict(list)
    for c in result.chunks:
        by_doc[c.doc_key].append(c)
    for doc in result.documents:
        chunks = by_doc[doc.doc_key]
        deps.chunks.put_document(doc, chunks)
        deps.keyword.delete(doc.doc_key)
        deps.keyword.add(chunks)
        if deps.vectors is not None:
            error = _write_vector_document(deps, doc.doc_key, chunks, snapshot_id)
            if error:
                vector_errors.append(error)
    tables: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in result.observations:
        tables[(o.source_id, o.table_key)].append(o)
    for (src, table), obs in tables.items():
        deps.observations.write_table(src, table, obs)
    return vector_errors


def reindex(deps: PipelineDeps, source_id: str | None = None, *, vectors_only: bool = False) -> int:
    """从 ChunkStore 的当前版本重建索引；vectors_only 只重试已记录的向量暂缺文档。"""
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
