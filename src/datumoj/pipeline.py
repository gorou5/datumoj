"""解析与索引流水线：manifest 里还没解析的快照 → 解析器 → chunk / 关键词 / 向量 / 观测值 / 目录。

只依赖契约（``datumoj.core.contracts``）；解析器用 ``discover_parsers`` 发现，存储由调用方注入。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

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


@dataclass
class ParseSummary:
    parsed: list[str] = field(default_factory=list)
    """成功解析的 snapshot_id。"""
    skipped: int = 0
    """已处理过（同一 sha256、同一解析器版本）而跳过的快照数。"""
    no_parser: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    """snapshot_id → 失败原因。"""


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
            if deps.manifest.get_parse(snap.sha256, cls.name, cls.version) is not None:
                summary.skipped += 1
                continue
            try:
                _process(deps, cls(), snap)
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
    return summary


def _process(deps: PipelineDeps, parser: Parser, snap: Snapshot) -> None:
    assert snap.sha256 is not None
    data = deps.raw.get(snap.sha256)
    result = parser.parse(snap, data, deps.manifest.snapshot_meta(snap.snapshot_id))
    result.check_against(snap)
    _dispatch(deps, result)
    if result.observations:
        state = ContentState.observations_loaded
    elif result.chunks:
        state = ContentState.content_fetched
    else:
        return
    deps.catalog.set_content_state(snap.resource_url, state)


def _dispatch(deps: PipelineDeps, result: ParseResult) -> None:
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
            deps.vectors.delete(doc.doc_key)
            deps.vectors.add(chunks)
    tables: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in result.observations:
        tables[(o.source_id, o.table_key)].append(o)
    for (src, table), obs in tables.items():
        deps.observations.write_table(src, table, obs)


def reindex(deps: PipelineDeps, source_id: str | None = None) -> int:
    """从 ChunkStore 的当前版本重建关键词索引（和向量索引，如果有）。返回重新索引的块数。"""
    source_ids = None if source_id is None else [source_id]
    chunks = list(deps.chunks.iter_chunks(source_ids))
    indexes = [deps.keyword] + ([deps.vectors] if deps.vectors is not None else [])
    for index in indexes:
        if source_id is None:
            index.clear()
        else:
            for doc_key in dict.fromkeys(c.doc_key for c in chunks):
                index.delete(doc_key)
        for i in range(0, len(chunks), _BATCH):
            index.add(chunks[i : i + _BATCH])
    return len(chunks)
