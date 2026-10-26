"""按注册表、抓取、解析和已发布块说明检索缺口。"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import fcntl

from datumoj.rikolto.core.contracts import (
    ChunkStore,
    Manifest,
    ParseStatus,
    Parser,
    Registry,
    discover_parsers,
    select_parser,
)
from datumoj.rikolto.core.models import (
    FetchOutcome,
    GapState,
    Hit,
    PipelineStatus,
    Snapshot,
    parse_context_fingerprint,
)

_SUCCESS = {FetchOutcome.ok, FetchOutcome.not_modified}
_GAP_ORDER = (
    GapState.out_of_scope,
    GapState.fetch_failed,
    GapState.not_ingested,
    GapState.not_indexed,
    GapState.zero_hits,
)
NOT_INDEXED_MAJORITY = 0.5
INDEX_REBUILDING = "rebuilding"
_REBUILD_LOCK = ".index-rebuild.lock"


@contextmanager
def rebuilding_index(data_dir: str | Path) -> Iterator[None]:
    """以进程锁公开 CLI 全量重建状态；进程退出时内核自动释放锁。"""
    path = Path(data_dir) / _REBUILD_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("索引重建已在进行中") from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def index_is_rebuilding(data_dir: str | Path | None) -> bool:
    if data_dir is None:
        return False
    try:
        lock = (Path(data_dir) / _REBUILD_LOCK).open("rb")
    except FileNotFoundError:
        return False
    with lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return False


def index_generation(retriever: object, *, data_dir: str | Path | None = None) -> str | None:
    """读取已暴露的代次；没有代次元数据时保留未知，不猜测。"""
    if index_is_rebuilding(data_dir):
        return INDEX_REBUILDING
    for index in (getattr(retriever, "keyword", None), getattr(retriever, "vector", None)):
        for name in ("index_generation", "generation"):
            value = getattr(index, name, None)
            if isinstance(value, str) and value:
                return value
    return None


def _has_ocr(document: object) -> bool:
    metadata = getattr(document, "metadata", {})
    pages = metadata.get("needs_ocr_pages", "") if isinstance(metadata, Mapping) else ""
    return isinstance(pages, str) and pages.isdecimal() and int(pages) > 0


def _ocr_snapshot_ids(
    source_id: str,
    snapshots: Sequence[Snapshot],
    *,
    registry: Registry,
    manifest: Manifest,
    chunks: ChunkStore,
    source_chunks: Sequence[object],
    parsers: Mapping[tuple[str, str], type[Parser]],
) -> set[str]:
    spec = registry.get(source_id)
    if spec is None:
        return set()
    documents_by_sha: dict[str, set[str]] = defaultdict(set)
    for chunk in source_chunks:
        documents_by_sha[chunk.snapshot_sha256].add(chunk.doc_key)

    result = set()
    for snapshot in snapshots:
        if snapshot.outcome not in _SUCCESS or snapshot.sha256 is None:
            continue
        keys = set()
        parser = select_parser(parsers, spec.kind, snapshot.content_type)
        if parser is not None:
            fingerprint = parse_context_fingerprint(snapshot, manifest.snapshot_meta(snapshot.snapshot_id))
            record = manifest.get_parse(snapshot.sha256, parser.name, parser.version, fingerprint)
            if record is not None and record.status is ParseStatus.ok and record.doc_keys:
                keys.update(record.doc_keys)
        if not keys:
            keys.update(documents_by_sha.get(snapshot.sha256, ()))
        if any(_has_ocr(chunks.get_document(key, snapshot.sha256)) for key in keys):
            result.add(snapshot.snapshot_id)
    return result


def _pipeline_status(
    source_ids: Sequence[str],
    *,
    registry: Registry,
    manifest: Manifest,
    chunks: ChunkStore,
    parsers: Mapping[tuple[str, str], type[Parser]],
    index_generation: str | None,
) -> PipelineStatus:
    parseable = parsed = parse_failed = needs_ocr = searchable = 0
    for source_id in source_ids:
        snapshots = manifest.snapshots_for(source_id)
        successful = [s for s in snapshots if s.outcome in _SUCCESS and s.sha256]
        parseable += len(successful)
        stats = manifest.parse_stats(source_id)
        parsed_count = stats.get(ParseStatus.ok, 0)
        parse_failed += stats.get(ParseStatus.failed, 0)
        source_chunks = list(chunks.iter_chunks([source_id]))
        searchable += len(source_chunks)
        chunk_shas = {chunk.snapshot_sha256 for chunk in source_chunks}
        # Earlier stores may contain published documents without a parse status row.
        parsed += min(len(successful), max(parsed_count, sum(s.sha256 in chunk_shas for s in successful)))
        needs_ocr += len(_ocr_snapshot_ids(
            source_id,
            successful,
            registry=registry,
            manifest=manifest,
            chunks=chunks,
            source_chunks=source_chunks,
            parsers=parsers,
        ))
    return PipelineStatus(
        parseable_snapshots=parseable,
        parsed=parsed,
        parse_failed=parse_failed,
        needs_ocr=needs_ocr,
        searchable_chunks=searchable,
        index_generation=index_generation,
    )


def pipeline_status(
    source_ids: Sequence[str] | None,
    *,
    registry: Registry,
    manifest: Manifest,
    chunks: ChunkStore,
    parsers: Mapping[tuple[str, str], type[Parser]] | None = None,
    index_generation: str | None = None,
) -> PipelineStatus:
    """统计当前来源范围；状态计数可以重叠，代次未知时为 None。"""
    selected_ids = set(source_ids) if source_ids else None
    ids = [spec.id for spec in registry.all() if selected_ids is None or spec.id in selected_ids]
    parser_map = parsers if parsers is not None else discover_parsers(skip_broken=True)
    return _pipeline_status(
        ids, registry=registry, manifest=manifest, chunks=chunks, parsers=parser_map,
        index_generation=index_generation,
    )


def _available(snapshots: Sequence[Snapshot]) -> str:
    snapshot = next((s for s in reversed(snapshots) if s.outcome in _SUCCESS and s.sha256), None)
    if snapshot is None:
        return "最近的可查内容：本来源无可回溯的成功快照"
    return (
        f"最近的可查内容：{snapshot.source_id} {snapshot.resource_url}，"
        f"快照 {snapshot.snapshot_id} sha256={snapshot.sha256}，抓取时间 {snapshot.fetched_at.isoformat()}"
    )


def _not_indexed(status: PipelineStatus) -> tuple[bool, str]:
    if status.index_generation == INDEX_REBUILDING:
        return True, "关键词索引正在重建"
    if status.parseable_snapshots and status.searchable_chunks == 0:
        unparsed = max(0, status.parseable_snapshots - status.parsed)
        return True, (
            f"已有成功快照，但当前没有可检索块；未解析 {unparsed}，"
            f"解析失败 {status.parse_failed}，needs_ocr {status.needs_ocr}"
        )
    unparsed = max(0, status.parseable_snapshots - status.parsed)
    incomplete = max(unparsed, status.parse_failed) + status.needs_ocr
    if status.parseable_snapshots and incomplete / status.parseable_snapshots > NOT_INDEXED_MAJORITY:
        return True, (
            f"管线未就绪：未解析 {unparsed}，解析失败 {status.parse_failed}，"
            f"needs_ocr {status.needs_ocr}；未就绪占比超过 {NOT_INDEXED_MAJORITY:.0%}"
        )
    return False, ""


def _all_parsed_without_documents(
    source_ids: Sequence[str],
    *,
    registry: Registry,
    manifest: Manifest,
    parsers: Mapping[tuple[str, str], type[Parser]],
) -> bool:
    """空的解析产物表示来源没有文本文档，而非索引漏建。"""
    seen = False
    for source_id in source_ids:
        spec = registry.get(source_id)
        if spec is None:
            continue
        for snapshot in manifest.snapshots_for(source_id):
            if snapshot.outcome not in _SUCCESS or snapshot.sha256 is None:
                continue
            seen = True
            parser = select_parser(parsers, spec.kind, snapshot.content_type)
            if parser is None:
                return False
            fingerprint = parse_context_fingerprint(snapshot, manifest.snapshot_meta(snapshot.snapshot_id))
            record = manifest.get_parse(snapshot.sha256, parser.name, parser.version, fingerprint)
            if record is None or record.status is not ParseStatus.ok or record.doc_keys != []:
                return False
    return seen


def gap_state(
    source_ids: Sequence[str] | None,
    hits: Sequence[Hit],
    *,
    registry: Registry,
    manifest: Manifest,
    chunks: ChunkStore | None = None,
    parsers: Mapping[tuple[str, str], type[Parser]] | None = None,
    index_generation: str | None = None,
) -> tuple[GapState, str | None]:
    """返回状态及逐来源说明；有 chunks 时按实际管线状态区分未就绪和零命中。"""
    if hits:
        return GapState.ok, None
    specs = registry.all()
    if not source_ids:
        selected_ids = [spec.id for spec in specs]
        if chunks is None:
            snapshots = sorted(
                (s for spec in specs for s in manifest.snapshots_for(spec.id)), key=lambda s: s.fetched_at,
            )
            return GapState.zero_hits, f"全部来源检索零命中；{_available(snapshots)}"
        parser_map = parsers if parsers is not None else discover_parsers(skip_broken=True)
        status = _pipeline_status(
            selected_ids, registry=registry, manifest=manifest, chunks=chunks,
            parsers=parser_map,
            index_generation=index_generation,
        )
        not_ready, reason = _not_indexed(status)
        if status.parseable_snapshots and status.searchable_chunks == 0 and _all_parsed_without_documents(
            selected_ids, registry=registry, manifest=manifest, parsers=parser_map,
        ):
            not_ready, reason = False, "全部成功快照已解析，解析器没有产生文本文档"
        snapshots = sorted(
            (s for source_id in selected_ids for s in manifest.snapshots_for(source_id)),
            key=lambda s: s.fetched_at,
        )
        if not_ready:
            return GapState.not_indexed, f"全部来源管线未就绪（{reason}）；{_available(snapshots)}"
        return GapState.zero_hits, f"全部来源检索零命中；{_available(snapshots)}"
    else:
        selected_ids = list(dict.fromkeys(source_ids))

    parser_map = parsers
    if chunks is not None and parser_map is None:
        parser_map = discover_parsers(skip_broken=True)
    states = []
    details = []
    for source_id in selected_ids:
        spec = registry.get(source_id)
        if spec is None:
            state, reason = GapState.out_of_scope, "注册表里没有这个来源；最近的可查内容：本来源不在范围内"
        else:
            snapshots = manifest.snapshots_for(source_id)
            successful = [s for s in snapshots if s.outcome in _SUCCESS and s.sha256]
            if successful and chunks is not None:
                status = _pipeline_status(
                    [source_id], registry=registry, manifest=manifest, chunks=chunks,
                    parsers=parser_map or {}, index_generation=index_generation,
                )
                not_ready, reason = _not_indexed(status)
                if status.parseable_snapshots and status.searchable_chunks == 0 and _all_parsed_without_documents(
                    [source_id], registry=registry, manifest=manifest, parsers=parser_map or {},
                ):
                    not_ready, reason = False, "全部成功快照已解析，解析器没有产生文本文档"
                state = GapState.not_indexed if not_ready else GapState.zero_hits
                if not not_ready:
                    reason = (
                        "全部成功快照已解析，解析器没有产生文本文档，检索零命中"
                        if status.searchable_chunks == 0 else "全部已解析且已有可检索块，检索零命中"
                    )
            elif any(s.outcome in _SUCCESS for s in snapshots):
                state, reason = GapState.zero_hits, "已有成功快照，检索零命中"
            elif snapshots:
                last = snapshots[-1]
                state = GapState.fetch_failed
                reason = f"最近一次抓取失败：{last.outcome.value} {last.error or ''}".strip()
            else:
                state, reason = GapState.not_ingested, "还没有成功快照"
            reason = f"{reason}；{_available(snapshots)}"
        states.append(state)
        details.append(f"{source_id}: {state.value}（{reason}）")
    return min(states, key=_GAP_ORDER.index), "；".join(details)
