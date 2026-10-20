"""分析工具层：MCP 和 CLI 共用的只读工具（PLAN 4.3）。

每个工具返回 ``ToolResult`` 的 JSON dict：``data``、``gap_state``、``freshness``、``warnings``、``gap_detail``。

- 来源给的正文和标签（块正文、标题、说明、调查名、SQL 结果里的字符串等）一律包进 ``SourceText``；
  ``warnings`` / ``gap_detail`` 只写本层的说明、标识符和校验过的日期，不拼接来源文本。
- 每条结果带出处（``Citation``：时间口径 + 许可署名），来源不是 ``redistributable`` 时标 ``usage_notice``。
- 参数不合法抛 ValueError；协议约定的错误（例如 ``QueryRejected``）原样抛出，由入口层转成错误结果。

只依赖 ``datumoj.core.contracts`` 的协议，具体实现由集成任务接线。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import datumoj
from datumoj.analysis.glossary import expand_terms, normalize
from datumoj.core.contracts import (
    CatalogFilter,
    CatalogHit,
    CatalogStore,
    ChunkStore,
    ContentState,
    CorruptedBlob,
    Manifest,
    NotFoundError,
    ObservationStore,
    ParseError,
    Parser,
    RawStore,
    Registry,
    Retriever,
    citation_for_chunk,
    citation_for_observation,
    discover_parsers,
    select_parser,
)
from datumoj.core.models import (
    Chunk,
    Citation,
    Document,
    FetchOutcome,
    Freshness,
    GapState,
    Hit,
    LicenseInfo,
    Observation,
    Snapshot,
    SourceText,
    TimeBasis,
    ToolResult,
    UsageScope,
    parse_locator,
)

__all__ = ["INTERNAL_ONLY", "AnalysisTools", "expand_terms"]

INTERNAL_ONLY = "仅供内部使用"
MAX_K = 50
MAX_CONTEXT = 10
MAX_ROWS = 1000

_SHA_RE = re.compile(r"[0-9a-f]{64}")
_SUCCESS = (FetchOutcome.ok, FetchOutcome.not_modified)
_AMENDMENT_DATES = ("promulgate_date", "enforcement_date")


# ---------- 小工具 ----------


def _st(value: str | None) -> SourceText | None:
    return None if value is None else SourceText(content=value)


def _result(
    data: Any,
    gap_state: GapState = GapState.ok,
    freshness: Freshness | None = None,
    warnings: Iterable[str] = (),
    gap_detail: str | None = None,
) -> dict[str, Any]:
    return ToolResult[Any](
        data=data, gap_state=gap_state, freshness=freshness or Freshness(),
        warnings=list(dict.fromkeys(warnings)), gap_detail=gap_detail,
    ).model_dump(mode="json")


def _freshness(times: Iterable[tuple[str, datetime]]) -> Freshness:
    """由结果用到的 (source_id, 抓取时间) 算新鲜度。"""
    times = list(times)
    if not times:
        return Freshness()
    by_source: dict[str, datetime] = {}
    for sid, t in times:
        by_source[sid] = max(by_source.get(sid, t), t)
    stamps = [t for _, t in times]
    return Freshness(as_of=max(stamps), oldest_fetched_at=min(stamps), by_source=by_source)


def _merge_freshness(items: Iterable[Freshness]) -> Freshness:
    items = [f for f in items if f.as_of is not None]
    if not items:
        return Freshness()
    by_source: dict[str, datetime] = {}
    for f in items:
        by_source.update(f.by_source)
    return Freshness(
        as_of=max(f.as_of for f in items),
        oldest_fetched_at=min(f.oldest_fetched_at or f.as_of for f in items),
        by_source=by_source,
    )


def _usage_notice(usage_scope: UsageScope | None) -> str | None:
    """没核对过（None）也按仅供内部使用处理。"""
    return None if usage_scope is UsageScope.redistributable else INTERNAL_ONLY


def _usage_warning(notices: Iterable[str | None], warnings: list[str]) -> None:
    if INTERNAL_ONLY in notices:
        warnings.append(f"部分结果的来源{INTERNAL_ONLY}（internal_only 或许可未核对），不得对外分发")


def _check_int(value: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{name} 必须是 {lo}–{hi} 的整数")
    return value


def _str_list(value: Any, name: str) -> list[str] | None:
    """None → None；单个字符串当作一项；空列表、空白项、非字符串抛 ValueError。"""
    if value is None:
        return None
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, Sequence) or not items:
        raise ValueError(f"{name} 必须是非空的字符串列表")
    if not all(isinstance(v, str) and v.strip() for v in items):
        raise ValueError(f"{name} 里不能有空白或非字符串")
    return list(items)


def _to_date(value: Any, name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ValueError(f"{name} 必须是日期或 YYYY-MM-DD")


def _iso_date(value: Any) -> date | None:
    """来源给的日期字符串：合法的 YYYY-MM-DD 才采用。"""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _cell(value: Any) -> Any:
    """SQL 结果里的字符串分不清是代码还是来源标签，一律当来源文本。"""
    return SourceText(content=value) if isinstance(value, str) else value


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = out.stdout.strip()
    return commit if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", commit) else None


# ---------- 工具 ----------


class AnalysisTools:
    """一组只读工具。依赖都按协议注入；``parsers`` 不给时按需 ``discover_parsers``（cite 用）。

    ``index_built_at`` 返回索引最近一次构建的时间（契约里没有这项，由接线方提供），不给就报未知。
    """

    def __init__(
        self,
        *,
        retriever: Retriever,
        catalog: CatalogStore,
        observations: ObservationStore,
        chunks: ChunkStore,
        manifest: Manifest,
        raw: RawStore,
        registry: Registry,
        parsers: Mapping[tuple[str, str], type[Parser]] | None = None,
        data_dir: str | Path | None = None,
        index_built_at: Callable[[], datetime | None] | None = None,
    ):
        self.retriever = retriever
        self.catalog = catalog
        self.observations = observations
        self.chunks = chunks
        self.manifest = manifest
        self.raw = raw
        self.registry = registry
        self.parsers = parsers
        self.data_dir = Path(data_dir if data_dir is not None else os.environ.get("DATUMOJ_DATA_DIR", "./data"))
        self.index_built_at = index_built_at

    # ----- 出处 -----

    def _snapshot(self, sha256: str, source_id: str | None = None) -> Snapshot | None:
        """内容是这个 sha256 的最近一次成功抓取（manifest 的列表按 fetched_at 升序）。"""
        snaps = [
            s for s in self.manifest.snapshots_by_sha256(sha256)
            if s.outcome is FetchOutcome.ok and (source_id is None or s.source_id == source_id)
        ]
        return snaps[-1] if snaps else None

    def _license(self, source_id: str) -> LicenseInfo | None:
        spec = self.registry.get(source_id)
        return spec.license if spec is not None else None

    def _chunk_citation(self, chunk: Chunk, warnings: list[str]) -> Citation | None:
        snap = self._snapshot(chunk.snapshot_sha256, chunk.source_id)
        if snap is None:
            warnings.append(f"{chunk.chunk_id} 找不到来源快照，出处不完整")
            return None
        return citation_for_chunk(chunk, snap, self._license(chunk.source_id))

    def _obs_citation(self, obs: Observation, warnings: list[str]) -> Citation | None:
        snap = self._snapshot(obs.snapshot_sha256, obs.source_id)
        if snap is None:
            warnings.append(f"观测值 {obs.obs_id} 找不到来源快照，出处不完整")
            return None
        return citation_for_observation(obs, snap, self._license(obs.source_id))

    # ----- 结果视图 -----

    @staticmethod
    def _chunk_view(chunk: Chunk, citation: Citation | None) -> dict[str, Any]:
        return {
            "chunk_id": chunk.chunk_id, "doc_key": chunk.doc_key, "locator": chunk.locator,
            "title_path": [SourceText(content=t) for t in chunk.title_path], "text": SourceText(content=chunk.text),
            "citation": citation, "usage_notice": _usage_notice(citation and citation.usage_scope),
        }

    @staticmethod
    def _obs_view(obs: Observation, citation: Citation | None) -> dict[str, Any]:
        return {
            "obs_id": obs.obs_id, "dataset_key": obs.dataset_key, "table_key": obs.table_key,
            "survey": _st(obs.survey), "indicator": _st(obs.indicator), "indicator_code": obs.indicator_code,
            "area_code": obs.area_code, "area_label": _st(obs.area_label), "time_label": _st(obs.time_label),
            "unit": _st(obs.unit), "value": obs.value, "note": _st(obs.note), "locator": obs.locator,
            "citation": citation, "usage_notice": _usage_notice(citation and citation.usage_scope),
        }

    @staticmethod
    def _law_info(doc: Document | None, warnings: list[str]) -> dict[str, Any] | None:
        """法令文档（metadata 里有 asof / pending_amendments）的施行基准日和未施行修改。"""
        if doc is None or not {"asof", "pending_amendments"} & set(doc.metadata):
            return None
        asof = _iso_date(doc.metadata.get("asof"))
        if asof is None:
            warnings.append(f"{doc.doc_key}：没有可用的施行基准日（asof），不能确定条文按哪天的施行状态")
        try:
            items = json.loads(doc.metadata.get("pending_amendments", "[]"))
        except json.JSONDecodeError:
            items = None
        if not isinstance(items, list):
            warnings.append(f"{doc.doc_key}：未施行修改列表无法读取，可能有已公布但尚未施行的修改")
            items = []
        pending = []
        for item in items:
            if not isinstance(item, dict):
                continue
            view: dict[str, Any] = {}
            for key, value in item.items():
                if key in _AMENDMENT_DATES and (d := _iso_date(value)) is not None:
                    view[key] = d
                elif isinstance(value, str):
                    view[key] = SourceText(content=value)
                elif value is None or isinstance(value, bool | int | float):
                    view[key] = value
            pending.append(view)
        if pending:
            warnings.append(
                f"{doc.doc_key}：有 {len(pending)} 项已公布但截至施行基准日 {asof or '（未知）'} 尚未施行的修改；"
                "条文是该基准日的施行状态，回答时要提示"
            )
        return {"asof": asof, "pending_amendments": pending}

    def _scope_gap(self, source_ids: Sequence[str] | None, empty_detail: str) -> tuple[GapState, str]:
        missing = [sid for sid in source_ids or () if self.registry.get(sid) is None]
        if missing:
            return GapState.out_of_scope, f"注册表里没有这些来源：{', '.join(missing)}"
        return GapState.zero_hits, empty_detail

    def _parsers(self) -> Mapping[tuple[str, str], type[Parser]]:
        if self.parsers is None:
            self.parsers = discover_parsers(skip_broken=True)
        return self.parsers

    # ----- search_docs -----

    def search_docs(
        self,
        queries: str | Sequence[str],
        k: int = 10,
        sources: str | Sequence[str] | None = None,
        since: date | str | None = None,
    ) -> dict[str, Any]:
        """文本检索（法令、公告等）。中文、英文查询按对照表追加日文词一起检索（``data.queries`` 是实际用的查询）。

        ``since`` 按块的发布日 / 施行日过滤，两者都没有的块会被排除。
        """
        qs = _str_list(queries, "queries")
        k = _check_int(k, "k", 1, MAX_K)
        source_ids = _str_list(sources, "sources")
        since_d = _to_date(since, "since")
        used = list(qs)
        for q in qs:
            nq = normalize(q)
            used.extend(t for t in expand_terms(q)["ja_terms"] if normalize(t) not in nq and t not in used)
        res = self.retriever.search(used, k=k, source_ids=source_ids, date_range=None if since_d is None else (since_d, None))
        warnings = list(res.warnings)
        hits = [self._hit_view(hit, warnings) for hit in res.data]
        _usage_warning((h["usage_notice"] for h in hits), warnings)
        return _result({"queries": used, "hits": hits}, res.gap_state, res.freshness, warnings, res.gap_detail)

    def _hit_view(self, hit: Hit, warnings: list[str]) -> dict[str, Any]:
        doc = self.chunks.get_document(hit.doc_key, hit.citation.snapshot_sha256)
        return {
            "chunk_id": hit.chunk_id, "doc_key": hit.doc_key, "locator": hit.locator, "score": hit.score,
            "matched_by": hit.matched_by, "snippet": hit.snippet, "citation": hit.citation,
            "usage_notice": _usage_notice(hit.citation.usage_scope), "law": self._law_info(doc, warnings),
        }

    # ----- search_catalog -----

    def search_catalog(
        self,
        query: str = "",
        k: int = 20,
        sources: str | Sequence[str] | None = None,
        publishers: str | Sequence[str] | None = None,
        formats: str | Sequence[str] | None = None,
        modified_since: datetime | str | None = None,
    ) -> dict[str, Any]:
        """数据集目录检索。``state`` 是 ContentState：目录查到（catalog_only）不等于正文已读到。"""
        if not isinstance(query, str):
            raise ValueError("query 必须是字符串（可以为空，表示只按条件列出）")
        k = _check_int(k, "k", 1, MAX_K)
        source_ids = _str_list(sources, "sources")
        filters = CatalogFilter(
            source_ids=source_ids, publishers=_str_list(publishers, "publishers"),
            formats=_str_list(formats, "formats"), modified_since=modified_since,
        )
        hits = self.catalog.search(query, filters, k)
        if not hits:
            state, detail = self._scope_gap(source_ids, "目录检索零命中")
            return _result({"datasets": []}, state, gap_detail=detail)
        warnings: list[str] = []
        items = [self._catalog_view(h) for h in hits]
        only_catalog = sum(1 for h in hits if h.state is ContentState.catalog_only)
        if only_catalog:
            warnings.append(f"{only_catalog} 个数据集只有目录信息（catalog_only），正文尚未入库，不能当作已读到内容")
        _usage_warning((i["usage_notice"] for i in items), warnings)
        freshness = _merge_freshness(self.manifest.freshness(sid) for sid in dict.fromkeys(h.dataset.source_id for h in hits))
        return _result({"datasets": items}, GapState.ok, freshness, warnings)

    def _catalog_view(self, hit: CatalogHit) -> dict[str, Any]:
        d = hit.dataset
        lic = self._license(d.source_id) or LicenseInfo()
        resources = []
        for entry in hit.resources:
            r = entry.resource
            snap = self.manifest.last_success(r.url)
            resources.append({
                "resource_id": r.resource_id, "url": r.url, "name": _st(r.name), "format": _st(r.format),
                "license": _st(r.license), "state": entry.state,
                "snapshot": None if snap is None else {"snapshot_sha256": snap.sha256, "fetched_at": snap.fetched_at},
            })
        return {
            "dataset_key": d.dataset_key, "source_id": d.source_id, "kind": d.kind, "title": _st(d.title),
            "description": _st(d.description), "publisher": _st(d.publisher), "tags": [_st(t) for t in d.tags],
            "license": _st(d.license), "state": hit.state, "score": hit.score, "resources": resources,
            "provenance": {
                "source_id": d.source_id, "url": d.url,
                "time_basis": TimeBasis.published if d.published_at else TimeBasis.no_data_date,
                "published_at": d.published_at, "modified_at": d.modified_at,
                "license": lic.name, "attribution": lic.attribution, "usage_scope": lic.usage_scope,
            },
            "usage_notice": _usage_notice(lic.usage_scope),
        }

    # ----- query_sql -----

    def query_sql(self, sql: str, max_rows: int = MAX_ROWS) -> dict[str, Any]:
        """在只读视图 ``observations`` 上执行单条 SELECT/WITH（由 ObservationStore 把关只读）。

        数值只能从这里取。``data.describe`` 是库里有哪些表和列；结果列带上 ``obs_id`` 才有逐条出处。
        字符串单元格一律包成 SourceText，value 列是十进制字符串或 null（绝不补零）。
        """
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("sql 不能为空白")
        max_rows = _check_int(max_rows, "max_rows", 1, MAX_ROWS)
        res = self.observations.query_readonly(sql, max_rows=max_rows)
        warnings = list(res.warnings)
        provenance = [self._obs_view(o, self._obs_citation(o, warnings)) for o in res.provenance]
        surveys = {o.survey for o in res.provenance}
        if len(surveys) > 1:
            warnings.append(
                f"结果涉及 {len(surveys)} 个不同的调查：不同调查的数据口径不同，不能直接相加，也不能直接算增长率"
            )
        if res.rows and "obs_id" not in res.columns:
            warnings.append("结果列里没有 obs_id，无法逐行给出出处；需要出处时在 SELECT 里带上 obs_id")
        _usage_warning((p["usage_notice"] for p in provenance), warnings)
        data = {
            "columns": res.columns, "rows": [[_cell(v) for v in row] for row in res.rows],
            "provenance": provenance, "describe": self._describe_view(),
        }
        freshness = _freshness([(o.source_id, o.fetched_at) for o in res.provenance])
        if not res.rows:
            return _result(data, GapState.zero_hits, freshness, warnings, "查询没有返回任何行")
        return _result(data, GapState.ok, freshness, warnings)

    def _describe_view(self) -> dict[str, Any]:
        catalog = self.observations.describe()
        return {
            "columns": catalog.columns,
            "tables": [
                {
                    "source_id": t.source_id, "table_key": t.table_key, "dataset_keys": t.dataset_keys,
                    "surveys": [SourceText(content=s) for s in t.surveys],
                    "indicators": [SourceText(content=s) for s in t.indicators],
                    "area_count": t.area_count, "time_start": t.time_start, "time_end": t.time_end, "rows": t.rows,
                }
                for t in catalog.tables
            ],
        }

    # ----- get_chunk -----

    def get_chunk(self, chunk_id: str, context: int = 1) -> dict[str, Any]:
        """取当前版本里的一个块，连同前后各 ``context`` 个块（同一文档、同一快照）。"""
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise ValueError("chunk_id 不能为空白")
        context = _check_int(context, "context", 0, MAX_CONTEXT)
        chunk = self.chunks.get_chunk(chunk_id)
        if chunk is None:
            return _result(
                {"status": "not_found", "chunk_id": chunk_id, "chunks": []}, GapState.zero_hits,
                gap_detail="当前版本里没有这个 chunk_id",
            )
        siblings = self.chunks.chunks_for(chunk.doc_key, chunk.snapshot_sha256)
        idx = next((i for i, c in enumerate(siblings) if c.chunk_id == chunk_id), None)
        window = [chunk] if idx is None else siblings[max(0, idx - context) : idx + context + 1]
        warnings: list[str] = []
        views = []
        for c in window:
            view = self._chunk_view(c, self._chunk_citation(c, warnings))
            view["is_target"] = c.chunk_id == chunk_id
            views.append(view)
        law = self._law_info(self.chunks.get_document(chunk.doc_key, chunk.snapshot_sha256), warnings)
        _usage_warning((v["usage_notice"] for v in views), warnings)
        snap = self._snapshot(chunk.snapshot_sha256, chunk.source_id)
        data = {
            "status": "ok", "chunk_id": chunk_id, "doc_key": chunk.doc_key,
            "snapshot_sha256": chunk.snapshot_sha256, "chunks": views, "law": law,
        }
        freshness = _freshness([(snap.source_id, snap.fetched_at)] if snap else [])
        return _result(data, GapState.ok, freshness, warnings)

    # ----- cite -----

    def cite(self, snapshot_sha256: str, locator: str) -> dict[str, Any]:
        """回到 raw：取快照原始字节、校验哈希、用对应解析器重新解析，按 locator 找回原文。

        定位不到时 ``data.status = "not_found"``，原因在 gap_detail。
        """
        if not isinstance(snapshot_sha256, str) or not _SHA_RE.fullmatch(snapshot_sha256):
            raise ValueError("snapshot_sha256 必须是 64 位小写十六进制")
        if not isinstance(locator, str):
            raise ValueError("locator 必须是字符串")
        parse_locator(locator)

        def not_found(reason: str) -> dict[str, Any]:
            data = {"status": "not_found", "snapshot_sha256": snapshot_sha256, "locator": locator, "passages": []}
            return _result(data, GapState.zero_hits, gap_detail=reason)

        snap = self._snapshot(snapshot_sha256)
        if snap is None:
            return not_found("manifest 里没有内容是这个 sha256 的成功抓取")
        try:
            self.raw.verify(snapshot_sha256)
            raw = self.raw.get(snapshot_sha256)
        except NotFoundError:
            return not_found("raw 里没有这个快照的原始字节")
        except CorruptedBlob:
            return not_found("raw 里的内容和 sha256 不一致，不能用来还原")
        spec = self.registry.get(snap.source_id)
        if spec is None:
            return not_found(f"注册表里没有来源 {snap.source_id}，无法选择解析器")
        parser_cls = select_parser(self._parsers(), spec.kind, snap.content_type)
        if parser_cls is None:
            return not_found(f"来源 {snap.source_id} 没有适用于这个快照的解析器")
        try:
            parsed = parser_cls().parse(snap, raw, self.manifest.snapshot_meta(snap.snapshot_id))
        except ParseError:
            return not_found(f"解析器 {parser_cls.name} 无法解析这个快照")
        chunks = [c for c in parsed.chunks if c.locator == locator]
        observations = [o for o in parsed.observations if o.locator == locator]
        if not chunks and not observations:
            return not_found("快照里定位不到这个 locator")
        warnings: list[str] = []
        docs = {d.doc_key: d for d in parsed.documents}
        passages = []
        for c in chunks:
            view = {"kind": "chunk", **self._chunk_view(c, citation_for_chunk(c, snap, spec.license))}
            view["law"] = self._law_info(docs.get(c.doc_key), warnings)
            passages.append(view)
        for o in observations:
            passages.append({"kind": "observation", **self._obs_view(o, citation_for_observation(o, snap, spec.license))})
        _usage_warning((p["usage_notice"] for p in passages), warnings)
        data = {"status": "ok", "snapshot_sha256": snapshot_sha256, "locator": locator, "passages": passages}
        return _result(data, GapState.ok, _freshness([(snap.source_id, snap.fetched_at)]), warnings)

    # ----- version / coverage -----

    def version(self) -> dict[str, Any]:
        """包版本、git 提交、数据目录、索引构建时间；验收时先用它确认宿主加载的是哪个版本。"""
        built = self.index_built_at() if self.index_built_at is not None else None
        commit = _git_commit()
        warnings = []
        if commit is None:
            warnings.append("取不到 git 提交（不在 git 仓库里或没有 git）")
        if built is None:
            warnings.append("索引构建时间未知")
        data = {
            "package": "datumoj", "version": datumoj.__version__, "git_commit": commit,
            "data_dir": str(self.data_dir.resolve()), "index_built_at": built,
        }
        return _result(data, warnings=warnings)

    def coverage(self) -> dict[str, Any]:
        """注册表里的全部来源：入库状态（ok / fetch_failed / not_ingested）、新鲜度、失败统计、许可。"""
        rows = []
        freshness = []
        warnings: list[str] = []
        for spec in self.registry.all():
            fresh = self.manifest.freshness(spec.id)
            snaps = self.manifest.snapshots_for(spec.id)
            failures = self.manifest.failure_summary(spec.id)
            if fresh.as_of is not None:
                status = GapState.ok
            elif snaps and snaps[-1].outcome not in _SUCCESS:  # snapshots_for 按 fetched_at 升序
                status = GapState.fetch_failed
            else:
                status = GapState.not_ingested
            if status is GapState.ok and failures.latest_failures:
                warnings.append(f"{spec.id}：{len(failures.latest_failures)} 个资源最近一次抓取失败，内容可能不是最新")
            lic = spec.license
            rows.append({
                "source_id": spec.id, "name": spec.name, "kind": spec.kind, "status": status, "freshness": fresh,
                "snapshots": len(snaps), "failure_counts": failures.counts,
                "failing_resources": len(failures.latest_failures),
                "license": {
                    "name": lic.name, "attribution": lic.attribution, "usage_scope": lic.usage_scope,
                    "redistribution": lic.redistribution,
                },
                "usage_notice": _usage_notice(lic.usage_scope),
            })
            freshness.append(fresh)
        if not rows:
            return _result({"sources": []}, GapState.zero_hits, gap_detail="注册表里没有任何来源")
        return _result({"sources": rows}, GapState.ok, _merge_freshness(freshness), warnings)
