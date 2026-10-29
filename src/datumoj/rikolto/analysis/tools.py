"""分析工具层：MCP 和 CLI 共用的只读工具（PLAN 4.3）。

每个工具返回 ``ToolResult`` 的 JSON dict：``data``、``gap_state``、``freshness``、``warnings``、``gap_detail``。

- 来源给的正文和标签（块正文、标题、说明、调查名、SQL 结果里的字符串等）一律包进 ``SourceText``；
  ``warnings`` / ``gap_detail`` 只写本层的说明、标识符和校验过的日期，不拼接来源文本。
- 每条结果带出处（``Citation``：时间口径 + 许可署名），来源不是 ``redistributable`` 时标 ``usage_notice``。
- 参数不合法抛 ValueError；协议约定的错误（例如 ``QueryRejected``）原样抛出，由入口层转成错误结果。

只依赖 ``datumoj.rikolto.core.contracts`` 的协议，具体实现由集成任务接线。
"""

from __future__ import annotations

import inspect
import json
import math
import re
import subprocess
import time as _time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import datumoj
from datumoj.rikolto.analysis.glossary import expand_terms, normalize
from datumoj.rikolto.core.citations import document_snapshot, select_snapshot
from datumoj.rikolto.core.contracts import (
    AmbiguousCitation,
    CatalogFilter,
    CatalogHit,
    CatalogStore,
    ChangeLog,
    ChunkStore,
    ContentState,
    CorruptedBlob,
    Manifest,
    NotFoundError,
    ObservationStore,
    ParseError,
    Parser,
    QueryError,
    QueryRejected,
    QueryTimeout,
    RawStore,
    Registry,
    Retriever,
    citation_for_chunk,
    citation_for_observation,
    discover_parsers,
    select_parser,
)
from datumoj.rikolto.core.models import (
    ChangeEvent,
    ChangeKind,
    Chunk,
    Citation,
    DocKind,
    Document,
    EvidenceState,
    FetchOutcome,
    Freshness,
    GapState,
    Hit,
    LicenseInfo,
    Observation,
    ObservationRef,
    PipelineStatus,
    Snapshot,
    SourceText,
    TimeBasis,
    ToolResult,
    UsageScope,
    parse_locator,
    redact_url,
)
from datumoj.rikolto.core.storage import load_storage_config
from datumoj.rikolto.index.coverage import gap_state as pipeline_gap_state
from datumoj.rikolto.index.coverage import index_generation, pipeline_status

__all__ = ["INTERNAL_ONLY", "AnalysisTools", "expand_terms"]

INTERNAL_ONLY = "仅供内部使用"
MAX_K = 50
MAX_FILTERED_CANDIDATES = 500
MAX_CONTEXT = 10
MAX_ROWS = 1000

_SHA_RE = re.compile(r"[0-9a-f]{64}")
_SUCCESS = (FetchOutcome.ok, FetchOutcome.not_modified)
_AMENDMENT_DATES = ("promulgate_date", "enforcement_date")
_monotonic = _time.monotonic


# ---------- 小工具 ----------


def _st(value: str | None) -> SourceText | None:
    return None if value is None else SourceText(content=value)


def _result(
    data: Any,
    gap_state: GapState = GapState.ok,
    freshness: Freshness | None = None,
    warnings: Iterable[str] = (),
    gap_detail: str | None = None,
    evidence_state: EvidenceState | None = None,
    evidence_inputs: dict[str, Any] | None = None,
    pipeline: PipelineStatus | None = None,
) -> dict[str, Any]:
    return ToolResult[Any](
        data=data, gap_state=gap_state, freshness=freshness or Freshness(),
        warnings=list(dict.fromkeys(warnings)), gap_detail=gap_detail,
        evidence_state=evidence_state, evidence_inputs=evidence_inputs, pipeline=pipeline,
    ).model_dump(mode="json")


def _remaining_timeout(deadline: float | None, timeout_s: float) -> float:
    remaining = timeout_s if deadline is None else deadline - _monotonic()
    if remaining <= 0:
        raise QueryTimeout(f"query_sql 整个请求超过 {timeout_s:g} 秒")
    return remaining


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


def _skip_json_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _json_object_value_span(text: str, start: int, name: str) -> tuple[int, int] | None:
    """Return the source span of an object's last member named ``name``."""
    decoder = json.JSONDecoder()
    index = _skip_json_space(text, start + 1)
    found = None
    while index < len(text) and text[index] != "}":
        key, index = decoder.raw_decode(text, index)
        index = _skip_json_space(text, index)
        if index >= len(text) or text[index] != ":":
            raise ValueError("invalid JSON object")
        value_start = _skip_json_space(text, index + 1)
        _, value_end = decoder.raw_decode(text, value_start)
        if key == name:
            found = (value_start, value_end)
        index = _skip_json_space(text, value_end)
        if index < len(text) and text[index] == ",":
            index = _skip_json_space(text, index + 1)
        elif index >= len(text) or text[index] != "}":
            raise ValueError("invalid JSON object")
    return found


def _json_array_item_span(text: str, start: int, wanted: int) -> tuple[int, int] | None:
    decoder = json.JSONDecoder()
    index = _skip_json_space(text, start + 1)
    item = 0
    while index < len(text) and text[index] != "]":
        value_start = index
        _, value_end = decoder.raw_decode(text, value_start)
        if item == wanted:
            return value_start, value_end
        item += 1
        index = _skip_json_space(text, value_end)
        if index < len(text) and text[index] == ",":
            index = _skip_json_space(text, index + 1)
        elif index >= len(text) or text[index] != "]":
            raise ValueError("invalid JSON array")
    return None


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


def _to_datetime(value: Any, name: str, *, end_of_day: bool = False) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.max if end_of_day else time.min, tzinfo=UTC)
    elif isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            try:
                day = date.fromisoformat(text)
            except ValueError as exc:
                raise ValueError(f"{name} 必须是带时区的 ISO-8601 日期时间或 YYYY-MM-DD") from exc
            result = datetime.combine(day, time.max if end_of_day else time.min, tzinfo=UTC)
        else:
            try:
                result = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"{name} 必须是带时区的 ISO-8601 日期时间或 YYYY-MM-DD") from exc
    else:
        raise ValueError(f"{name} 必须是带时区的 ISO-8601 日期时间或 YYYY-MM-DD")
    if result.utcoffset() is None:
        raise ValueError(f"{name} 必须带时区")
    return result


def _iso_date(value: Any) -> date | None:
    """来源给的日期字符串：合法的 YYYY-MM-DD 才采用。"""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


_SQL_PART = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/|([A-Za-z_][A-Za-z_0-9]*)|([(),])"
)
_AGGREGATES = {"any_value", "array_agg", "avg", "count", "first", "last", "list", "max", "median", "min", "string_agg", "sum"}
_SIMPLE_LITERAL = r"(?:(?:DATE\s+)?'(?:''|[^'])*'|[-+]?(?:\d+(?:\.\d*)?|\.\d+)|NULL)"
_SIMPLE_FILTER = re.compile(
    rf"^\s*(source_id|table_key|time_start|time_end|time_label|area_code|area_label|value)\s*"
    rf"(?:=|!=|<>|>=|<=|>|<)\s*({_SIMPLE_LITERAL})\s*$|"
    rf"^\s*(source_id|table_key|time_start|time_end|time_label|area_code|area_label|value)\s+"
    rf"(?:IN\s*\(\s*{_SIMPLE_LITERAL}(?:\s*,\s*{_SIMPLE_LITERAL})*\s*\)|"
    rf"BETWEEN\s+{_SIMPLE_LITERAL}\s+AND\s+{_SIMPLE_LITERAL})\s*$",
    re.IGNORECASE,
)


def _sql_tokens(sql: str) -> list[tuple[str, int, int, int]]:
    """Return unquoted SQL words with source positions and parenthesis depth."""
    tokens = []
    depth = 0
    for match in _SQL_PART.finditer(sql):
        word, punctuation = match.group(1), match.group(2)
        if punctuation == ")":
            depth = max(0, depth - 1)
        if word is not None:
            tokens.append((word.lower(), match.start(1), match.end(1), depth))
        elif punctuation == "(":
            depth += 1
        elif punctuation == ")" or punctuation == ",":
            tokens.append((punctuation, match.start(2), match.end(2), depth))
    return tokens


def _reads_observations(sql: str) -> bool:
    tokens = _sql_tokens(sql)
    return any(
        word in {"from", "join"} and index + 1 < len(tokens) and tokens[index + 1][0] == "observations"
        for index, (word, _, _, _) in enumerate(tokens)
    )


def _where_conditions(sql: str) -> list[str]:
    """Extract top-level WHERE terms joined by AND; complex terms remain verbatim."""
    tokens = _sql_tokens(sql)
    where = next((token for token in tokens if token[0] == "where" and token[3] == 0), None)
    if where is None:
        return []
    end = next((
        token[1] for token in tokens
        if token[1] > where[2] and token[3] == 0 and token[0] in {
            "group", "having", "qualify", "order", "limit", "offset", "union", "except", "intersect"
        }
    ), len(sql))
    conditions = []
    start = where[2]
    between_and_pending = False
    for token in tokens:
        if token[3] != 0 or not where[2] < token[1] < end:
            continue
        if token[0] == "between":
            between_and_pending = True
            continue
        if token[0] != "and":
            continue
        if between_and_pending:
            between_and_pending = False
            continue
        conditions.append(sql[start:token[1]].strip())
        start = token[2]
    if sql[start:end].strip():
        conditions.append(sql[start:end].strip())
    return conditions


def _has_aggregate(sql: str) -> bool:
    tokens = _sql_tokens(sql)
    return any(
        word in _AGGREGATES and sql[end:].lstrip().startswith("(")
        for word, _, end, _ in tokens
    )


def _iso_datetime(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime | date) else (str(value) if value is not None else None)


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
        change_log: ChangeLog | None = None,
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
        self.change_log = change_log
        self.parsers = parsers
        self.data_dir = load_storage_config(data_dir=data_dir).data_dir
        self.index_built_at = index_built_at

    # ----- 出处 -----

    def _snapshot(
        self, sha256: str, source_id: str | None = None, *,
        resource_url: str | None = None, snapshot_id: str | None = None,
        warnings: list[str] | None = None,
    ) -> Snapshot | None:
        try:
            return select_snapshot(
                self.manifest, sha256, source_id, resource_url=resource_url, snapshot_id=snapshot_id,
            )
        except AmbiguousCitation:
            if warnings is not None:
                warnings.append("来源快照有歧义，需 snapshot_id 或资源 URL")
                return None
            raise

    def _document_snapshot(
        self, sha256: str, source_id: str, doc_key: str, warnings: list[str],
        doc: Document | None = None, resource_url: str | None = None,
    ) -> Snapshot | None:
        if doc is None and self.chunks is not None:
            doc = self.chunks.get_document(doc_key, sha256)
        try:
            return document_snapshot(self.manifest, sha256, source_id, doc_key, doc, resource_url=resource_url)
        except AmbiguousCitation:
            warnings.append("文档来源快照有歧义，无法确定资源 URL")
            return None

    def _license(self, source_id: str) -> LicenseInfo | None:
        spec = self.registry.get(source_id)
        return spec.license if spec is not None else None

    def _chunk_citation(self, chunk: Chunk, warnings: list[str]) -> Citation | None:
        snap = self._document_snapshot(chunk.snapshot_sha256, chunk.source_id, chunk.doc_key, warnings)
        if snap is None:
            warnings.append(f"{chunk.chunk_id} 找不到来源快照，出处不完整")
            return None
        return citation_for_chunk(chunk, snap, self._license(chunk.source_id))

    def _obs_citation(self, obs: Observation, warnings: list[str]) -> Citation | None:
        snap = self._document_snapshot(
            obs.snapshot_sha256, obs.source_id, f"{obs.source_id}:{obs.table_key}", warnings,
        )
        if snap is None:
            warnings.append(f"观测值 {obs.obs_id} 找不到来源快照，出处不完整")
            return None
        return citation_for_observation(obs, snap, self._license(obs.source_id)).model_copy(
            update={"fetched_at": snap.fetched_at},
        )

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
            "obs_id": obs.obs_id, "source_id": obs.source_id, "table_key": obs.table_key,
            "observation_ref": str(ObservationRef(
                source_id=obs.source_id, table_key=obs.table_key, obs_id=obs.obs_id,
            )),
            "dataset_key": obs.dataset_key,
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
        kinds: str | Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """文本检索（法令、公告等）。中文、英文查询按对照表追加日文词一起检索（``data.queries`` 是实际用的查询）。

        ``since`` 按块的发布日 / 施行日过滤，两者都没有的块会被排除；``kinds`` 按文档类型过滤。
        """
        qs = _str_list(queries, "queries")
        k = _check_int(k, "k", 1, MAX_K)
        source_ids = _str_list(sources, "sources")
        kind_values = _str_list(kinds, "kinds")
        doc_kinds = None
        if kind_values is not None:
            try:
                doc_kinds = [DocKind(value) for value in kind_values]
            except ValueError as exc:
                raise ValueError(f"kinds 只能包含 {', '.join(kind.value for kind in DocKind)}") from exc
        since_d = _to_date(since, "since")
        used = list(qs)
        for q in qs:
            nq = normalize(q)
            used.extend(t for t in expand_terms(q)["ja_terms"] if normalize(t) not in nq and t not in used)
        date_range = None if since_d is None else (since_d, None)
        doc_keys = None
        if doc_kinds is not None:
            doc_keys = sorted({
                chunk.doc_key for chunk in self.chunks.iter_chunks(source_ids)
                if (doc := self.chunks.get_document(chunk.doc_key, chunk.snapshot_sha256)) is not None
                and doc.kind in doc_kinds
            })
        filtered_search = getattr(self.retriever, "search_filtered", None)
        fallback_candidates = False
        if callable(filtered_search):
            res = filtered_search(
                used, k=k, source_ids=source_ids, date_range=date_range, doc_keys=doc_keys,
            )
        else:
            fallback_candidates = doc_kinds is not None
            res = self.retriever.search(
                used, k=MAX_FILTERED_CANDIDATES if fallback_candidates else k,
                source_ids=source_ids, date_range=date_range,
            )
        hits = list(res.data)
        if doc_kinds is not None:
            hits = [
                hit for hit in hits
                if (doc := self.chunks.get_document(hit.doc_key, hit.citation.snapshot_sha256)) is not None
                and doc.kind in doc_kinds
            ][:k]
        warnings = list(res.warnings)
        truncated = fallback_candidates and len(res.data) >= MAX_FILTERED_CANDIDATES and len(hits) < k
        if truncated:
            warnings.append(
                f"truncated=true：候选补取达到上限 {MAX_FILTERED_CANDIDATES}，结果可能不完整"
            )
        views = [self._hit_view(hit, warnings) for hit in hits]
        _usage_warning((h["usage_notice"] for h in views), warnings)
        generation = index_generation(self.retriever, data_dir=self.data_dir)
        pipeline = pipeline_status(
            source_ids,
            registry=self.registry,
            manifest=self.manifest,
            chunks=self.chunks,
            parsers=self._parsers(),
            index_generation=generation,
        )
        gap_state, gap_detail = pipeline_gap_state(
            source_ids,
            res.data if hits else (),
            registry=self.registry,
            manifest=self.manifest,
            chunks=self.chunks,
            parsers=self._parsers(),
            index_generation=generation,
        )
        if doc_kinds is not None and not hits and gap_state is GapState.zero_hits and not truncated:
            gap_detail = "指定的文档类型没有命中"
        if truncated and gap_state is GapState.zero_hits:
            gap_state, gap_detail = GapState.ok, None
        freshness = _freshness((hit.citation.source_id, hit.citation.fetched_at) for hit in hits)
        return _result(
            {"queries": used, "hits": views}, gap_state, freshness, warnings, gap_detail, pipeline=pipeline,
        )

    def _hit_view(self, hit: Hit, warnings: list[str]) -> dict[str, Any]:
        doc = self.chunks.get_document(hit.doc_key, hit.citation.snapshot_sha256)
        return {
            "chunk_id": hit.chunk_id, "doc_key": hit.doc_key, "locator": hit.locator, "score": hit.score,
            "matched_by": hit.matched_by, "snippet": hit.snippet, "citation": hit.citation,
            "usage_notice": _usage_notice(hit.citation.usage_scope), "law": self._law_info(doc, warnings),
        }

    # ----- list_changes -----

    def list_changes(
        self,
        since: datetime | date | str | None = None,
        until: datetime | date | str | None = None,
        sources: str | Sequence[str] | None = None,
        kinds: str | Sequence[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """列出变化日志；默认窗口是最近七天，事件按检测时间升序。"""
        since_dt = _to_datetime(since, "since")
        if since_dt is None:
            since_dt = datetime.now(UTC) - timedelta(days=7)
        until_dt = _to_datetime(until, "until", end_of_day=True)
        if until_dt is not None and since_dt > until_dt:
            raise ValueError("since 不能晚于 until")
        source_ids = _str_list(sources, "sources")
        kind_values = _str_list(kinds, "kinds")
        change_kinds = None
        if kind_values is not None:
            try:
                change_kinds = [ChangeKind(value) for value in kind_values]
            except ValueError as exc:
                raise ValueError(f"kinds 只能包含 {', '.join(kind.value for kind in ChangeKind)}") from exc
        limit = _check_int(limit, "limit", 0, MAX_ROWS)
        if self.change_log is None:
            return _result([], GapState.not_ingested, gap_detail="变化日志尚未接入工具层")

        events = self.change_log.list(
            since=since_dt, until=until_dt, source_ids=source_ids, kinds=change_kinds, limit=limit,
        )
        warnings: list[str] = []
        freshness: list[tuple[str, datetime]] = []
        rows = []
        fallback_docs: dict[tuple[str, str], dict[str, None]] | None = None
        for event in events:
            snapshot_sha = (
                event.old_sha256 if event.kind is ChangeKind.removed else (event.new_sha256 or event.old_sha256)
            )
            doc = self.chunks.get_document(event.doc_key, snapshot_sha) if event.doc_key and snapshot_sha else None
            if doc is None and snapshot_sha is not None:
                if fallback_docs is None:
                    fallback_docs = {}
                    for item in self.chunks.iter_chunks(list(dict.fromkeys(e.source_id for e in events))):
                        key = (item.source_id, item.snapshot_sha256)
                        fallback_docs.setdefault(key, {})[item.doc_key] = None
                for key in fallback_docs.get((event.source_id, snapshot_sha), {}):
                    candidate = self.chunks.get_document(key, snapshot_sha)
                    candidate_snap = self._document_snapshot(
                        snapshot_sha, event.source_id, key, warnings, candidate,
                    )
                    if event.resource_url is None or (
                        candidate_snap is not None and candidate_snap.resource_url == event.resource_url
                    ):
                        doc = candidate
                        break
            snap = None
            if snapshot_sha is not None:
                snap = self._document_snapshot(
                    snapshot_sha, event.source_id, event.doc_key or (doc.doc_key if doc else ""),
                    warnings, doc, event.resource_url,
                )
            citation = self._change_citation(event, snap, doc)
            if citation is None:
                warnings.append(f"变化事件 {event.event_id} 缺少可引用的成功快照")
            else:
                freshness.append((event.source_id, citation.fetched_at))
            rows.append({
                "kind": event.kind, "source_id": event.source_id, "doc_key": event.doc_key,
                "title": None if doc is None else _st(doc.title),
                "summary": SourceText(content=event.summary), "detected_at": event.detected_at,
                "citation": citation,
            })
        return _result(rows, GapState.ok if rows else GapState.zero_hits, _freshness(freshness), warnings)

    def _change_citation(
        self, event: ChangeEvent, snap: Snapshot | None, doc: Document | None,
    ) -> Citation | None:
        if snap is None or snap.sha256 is None:
            return None
        license_info = self._license(event.source_id)
        if doc is not None:
            chunks = self.chunks.chunks_for(doc.doc_key, doc.snapshot_sha256)
            chunk = (
                next((c for c in chunks if c.locator == event.locator), None)
                if event.locator else next(iter(chunks), None)
            )
            if chunk is not None:
                return citation_for_chunk(chunk, snap, license_info)
        if event.locator is None:
            return None
        basis = TimeBasis.effective if doc and doc.effective_at else (
            TimeBasis.published if doc and doc.published_at else TimeBasis.no_data_date
        )
        return Citation(
            snapshot_id=snap.snapshot_id,
            source_id=event.source_id, resource_url=snap.resource_url, snapshot_sha256=snap.sha256,
            fetched_at=snap.fetched_at, locator=event.locator,
            published_at=doc.published_at if doc else None, effective_at=doc.effective_at if doc else None,
            time_basis=basis, license=license_info.name if license_info else None,
            attribution=license_info.attribution if license_info else None,
            usage_scope=license_info.usage_scope if license_info else None,
        )

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
        items = [self._catalog_view(h, warnings) for h in hits]
        only_catalog = sum(1 for h in hits if h.state is ContentState.catalog_only)
        if only_catalog:
            warnings.append(f"{only_catalog} 个数据集只有目录信息（catalog_only），正文尚未入库，不能当作已读到内容")
        _usage_warning((i["usage_notice"] for i in items), warnings)
        freshness = _merge_freshness(self.manifest.freshness(sid) for sid in dict.fromkeys(h.dataset.source_id for h in hits))
        return _result({"datasets": items}, GapState.ok, freshness, warnings)

    def _catalog_view(self, hit: CatalogHit, warnings: list[str]) -> dict[str, Any]:
        d = hit.dataset
        lic = self._license(d.source_id) or LicenseInfo()
        citation = None
        if d.snapshot_sha256 is None or d.locator is None:
            warnings.append("该记录缺少快照引用，重新解析后可还原")
        else:
            snap = self._snapshot(d.snapshot_sha256, d.source_id, warnings=warnings)
            if snap is None:
                warnings.append("该记录引用的快照不在 manifest 中，无法生成 citation")
            else:
                citation = Citation(
                    snapshot_id=snap.snapshot_id,
                    source_id=d.source_id, resource_url=snap.resource_url, snapshot_sha256=snap.sha256,
                    fetched_at=snap.fetched_at, locator=d.locator,
                    published_at=d.modified_at.date() if d.modified_at is not None else None,
                    time_basis=TimeBasis.published if d.modified_at is not None else TimeBasis.no_data_date,
                    license=lic.name, attribution=lic.attribution, usage_scope=lic.usage_scope,
                )
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
            "citation": citation,
            "provenance": {
                "source_id": d.source_id, "url": d.url,
                "time_basis": TimeBasis.published if d.published_at else TimeBasis.no_data_date,
                "published_at": d.published_at, "modified_at": d.modified_at,
                "license": lic.name, "attribution": lic.attribution, "usage_scope": lic.usage_scope,
            },
            "usage_notice": _usage_notice(lic.usage_scope),
        }

    # ----- query_sql -----

    def query_sql(
        self, sql: str, max_rows: int = MAX_ROWS, timeout_s: float = 10, *,
        as_of_release: date | str | None = None, generation_token: str | None = None,
    ) -> dict[str, Any]:
        """在只读视图 ``observations`` 上执行单条 SELECT/WITH（由 ObservationStore 把关只读）。

        数值只能从这里取。``data.describe`` 是库里有哪些表和列；要明确逐行出处时 SELECT 带上
        ``source_id``、``table_key``、``obs_id``。
        字符串单元格一律包成 SourceText，value 列是十进制字符串或 null（绝不补零）。
        ``timeout_s`` 限制整个请求，包括查询、出处和元数据处理。
        ``as_of_release`` 是来源发布标识或 date（含当天）；``generation_token`` 固定数据代次。
        发布感知逐行出处须保留 source_id、table_key、obs_id、snapshot_sha256、release_id。
        """
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError("sql 不能为空白")
        max_rows = _check_int(max_rows, "max_rows", 1, MAX_ROWS)
        try:
            valid_timeout = (
                not isinstance(timeout_s, bool) and isinstance(timeout_s, (int, float))
                and math.isfinite(float(timeout_s)) and timeout_s > 0
            )
        except (OverflowError, TypeError, ValueError):
            valid_timeout = False
        if not valid_timeout:
            raise ValueError("timeout_s 必须是有限正数")
        timeout_s = float(timeout_s)
        deadline = _monotonic() + timeout_s
        try:
            release_options = {}
            if as_of_release is not None:
                release_options['as_of_release'] = as_of_release
            if generation_token is not None:
                release_options['generation_token'] = generation_token
            res = self.observations.query_readonly(
                sql, max_rows=max_rows, timeout_s=_remaining_timeout(deadline, timeout_s), **release_options,
            )
            _remaining_timeout(deadline, timeout_s)
            warnings = list(res.warnings)
            query_observations = res.provenance
            # Preserve legacy three-column citations for calls without new options.
            # Explicit release-aware calls use the original five-column evidence.
            # Calls without options keep the legacy response shape and citations.
            reference_cols = ('source_id', 'table_key', 'obs_id')
            if (res.generation_token is not None and not release_options and not query_observations
                    and all(res.columns.count(c) == 1 for c in reference_cols) and res.rows):
                refs = dict.fromkeys(tuple(row[res.columns.index(c)] for c in reference_cols) for row in res.rows)
                conditions = []
                for ref in refs:
                    if all(isinstance(value, str) for value in ref):
                        conditions.append('(' + ','.join("'" + value.replace("'", "''") + "'" for value in ref) + ')')
                if conditions:
                    cited = self.observations.query_readonly(
                        'SELECT * FROM observations WHERE (source_id,table_key,obs_id) IN (VALUES ' + ','.join(conditions) + ')' , max_rows=MAX_ROWS,
                        timeout_s=_remaining_timeout(deadline, timeout_s), generation_token=res.generation_token,
                    )
                    by_ref = {(o.source_id, o.table_key, o.obs_id): o for o in cited.provenance}
                    query_observations = [by_ref[ref] for ref in refs if ref in by_ref]
            provenance = []
            for observation in query_observations:
                _remaining_timeout(deadline, timeout_s)
                citation = self._obs_citation(observation, warnings)
                if res.generation_token is not None:
                    matches = [s for s in self.manifest.snapshots_by_sha256(observation.snapshot_sha256)
                               if s.source_id == observation.source_id and s.fetched_at == observation.fetched_at
                               and (citation is None or s.resource_url == citation.resource_url)]
                    if len({s.resource_url for s in matches}) == 1:
                        citation = citation_for_observation(observation, matches[-1], self._license(observation.source_id))
                    else:
                        citation = None
                view = self._obs_view(observation, citation)
                if release_options and res.generation_token is not None:
                    view.update(observation_identity=str(observation.identity), release_id=observation.release_id,
                                released_at=observation.released_at)
                provenance.append(view)
                _remaining_timeout(deadline, timeout_s)
            surveys = {o.survey for o in query_observations}
            if len(surveys) > 1:
                warnings.append(
                    f"结果涉及 {len(surveys)} 个不同的调查：不同调查的数据口径不同，不能直接相加，也不能直接算增长率"
                )
            if res.rows and "obs_id" not in res.columns:
                warnings.append("结果列里没有 obs_id，无法逐行给出出处；需要出处时在 SELECT 里带上 obs_id")
            evidence_state, linked_rows = self._query_evidence_state(
                sql, res.rows, res.columns, query_observations, provenance,
            )
            _remaining_timeout(deadline, timeout_s)
            if evidence_state is EvidenceState.unavailable:
                warnings.append(
                    "结果没有可用的逐行出处；请查询原始观测行，并在 SELECT 里保留 source_id、table_key、obs_id，"
                    "同时移除 count/sum/avg 等聚合或按观测行展开结果"
                )
            elif evidence_state is EvidenceState.partial:
                warnings.append(
                    f"仅 {linked_rows}/{len(res.rows)} 行有可用出处；请在 SELECT 里保留 source_id、table_key、obs_id，"
                    "并按观测行查询，避免聚合或丢失行标识"
                )
            _usage_warning((p["usage_notice"] for p in provenance), warnings)
            if release_options and res.generation_token is not None:
                evidence_state = res.evidence_state
                evidence_inputs = res.evidence_inputs
            else:
                evidence_inputs = self._query_evidence_inputs(
                    sql, deadline=deadline, timeout_s=timeout_s,
                    query_options={'generation_token': res.generation_token} if res.generation_token else None,
                )
            data = {
                "columns": res.columns, "rows": [[_cell(v) for v in row] for row in res.rows],
                "provenance": provenance,
                "describe": self._describe_view(deadline=deadline, timeout_s=timeout_s),
            }
            if release_options and res.generation_token is not None:
                data['generation_token'] = res.generation_token
                data['row_evidence'] = [row.model_dump(mode='json') for row in res.row_evidence]
            freshness = _freshness([(o.source_id, o.fetched_at) for o in query_observations])
            _remaining_timeout(deadline, timeout_s)
            if not res.rows:
                result = _result(
                    data, GapState.zero_hits, freshness, warnings, "查询没有返回任何行",
                    evidence_state, evidence_inputs,
                )
            else:
                result = _result(data, GapState.ok, freshness, warnings, evidence_state=evidence_state,
                                 evidence_inputs=evidence_inputs)
            _remaining_timeout(deadline, timeout_s)
            return result
        except QueryTimeout as exc:
            raise QueryTimeout(f"query_sql 整个请求超过 {timeout_s:g} 秒") from exc

    def _query_evidence_state(
        self, sql: str, rows: list[tuple[Any, ...]], columns: list[str],
        observations: list[Observation], provenance: list[dict[str, Any]],
    ) -> tuple[EvidenceState, int]:
        """Count result rows that uniquely link to an observation with a snapshot citation."""
        if not rows or "obs_id" not in columns:
            return EvidenceState.unavailable, 0
        indexes = {name: columns.index(name) for name in ("source_id", "table_key", "obs_id") if name in columns}
        by_id: dict[str, list[Observation]] = {}
        citations: dict[tuple[str, str, str], dict[str, Any]] = {}
        for observation, view in zip(observations, provenance, strict=True):
            by_id.setdefault(observation.obs_id, []).append(observation)
            citations[(observation.source_id, observation.table_key, observation.obs_id)] = view["citation"]
        linked = 0
        obs_index = indexes["obs_id"]
        identity = [name for name in ("source_id", "table_key") if name in indexes]
        for row in rows:
            obs_id = row[obs_index]
            if not isinstance(obs_id, str):
                continue
            matches = by_id.get(obs_id, [])
            if identity:
                matches = [
                    observation for observation in matches
                    if all(getattr(observation, name) == row[indexes[name]] for name in identity)
                ]
            if len(matches) == 1 and citations.get(
                (matches[0].source_id, matches[0].table_key, matches[0].obs_id)
            ) is not None:
                linked += 1
        if linked == 0:
            return EvidenceState.unavailable, linked
        if linked < len(rows) or _has_aggregate(sql):
            return EvidenceState.partial, linked
        return EvidenceState.complete, linked

    def _query_evidence_inputs(
        self, sql: str, *, deadline: float | None = None, timeout_s: float = 10,
        query_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        conditions = _where_conditions(sql)
        time_fields = {"time_start", "time_end", "time_label"}
        area_fields = {"area_code", "area_label"}

        def raw_filters(fields: set[str]) -> list[str] | str:
            matching = [condition for condition in conditions if fields & {token[0] for token in _sql_tokens(condition)}]
            return list(dict.fromkeys(matching)) if matching else "未识别"

        table_rows: list[tuple[Any, ...]] = []
        if _reads_observations(sql):
            table_rows = self._query_all_rows(
                "SELECT source_id, table_key, snapshot_sha256, min(fetched_at), max(fetched_at) "
                "FROM observations GROUP BY source_id, table_key, snapshot_sha256 "
                "ORDER BY source_id, table_key, snapshot_sha256",
                deadline=deadline, timeout_s=timeout_s, query_options=query_options,
            )
            if conditions and all(_SIMPLE_FILTER.fullmatch(condition) for condition in conditions):
                where = " AND ".join(f"({condition})" for condition in conditions)
                try:
                    matching = self._query_all_rows(
                        "SELECT DISTINCT source_id, table_key FROM observations WHERE " + where
                        + " ORDER BY source_id, table_key",
                        deadline=deadline, timeout_s=timeout_s, query_options=query_options,
                    )
                    used = {(sid, table) for sid, table in matching}
                    table_rows = [row for row in table_rows if (row[0], row[1]) in used]
                except QueryTimeout:
                    raise
                except (QueryError, QueryRejected):
                    # Complex dialect details do not make the original successful query fail.
                    pass

        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for source_id, table_key, snapshot_sha256, first, last in table_rows:
            _remaining_timeout(deadline, timeout_s)
            key = (source_id, table_key)
            table = grouped.setdefault(key, {"source_id": source_id, "table_key": table_key, "snapshots": [], "times": []})
            snapshot_rows = self.manifest.snapshots_by_sha256(snapshot_sha256)
            _remaining_timeout(deadline, timeout_s)
            matching_snapshots = [snapshot for snapshot in snapshot_rows if snapshot.source_id == source_id]
            table["snapshots"].append({
                "sha256": snapshot_sha256,
                "snapshot_ids": [
                    {"snapshot_id": snapshot.snapshot_id, "fetched_at": snapshot.fetched_at.isoformat()}
                    for snapshot in matching_snapshots
                ],
            })
            table["times"].extend(value for value in (first, last) if value is not None)
        tables = []
        for key in sorted(grouped):
            _remaining_timeout(deadline, timeout_s)
            table = grouped[key]
            times = sorted(_iso_datetime(value) for value in table.pop("times"))
            tables.append({
                **table,
                "snapshots": sorted(table["snapshots"], key=lambda item: item["sha256"]),
                "fetched_at_range": {"from": times[0] if times else None, "to": times[-1] if times else None},
            })
        return {"tables": tables, "filters": {"time": raw_filters(time_fields), "area": raw_filters(area_fields)}}

    def _query_all_rows(
        self, sql: str, *, deadline: float | None = None, timeout_s: float = 10,
        query_options: dict[str, Any] | None = None,
    ) -> list[tuple[Any, ...]]:
        """Read query pages so an input inventory can exceed the user result row limit."""
        rows = []
        offset = 0
        while True:
            page = self.observations.query_readonly(
                f"{sql} LIMIT {MAX_ROWS} OFFSET {offset}", max_rows=MAX_ROWS,
                timeout_s=_remaining_timeout(deadline, timeout_s), **(query_options or {}),
            ).rows
            _remaining_timeout(deadline, timeout_s)
            rows.extend(page)
            if len(page) < MAX_ROWS:
                return rows
            offset += MAX_ROWS

    def _describe_view(self, *, deadline: float | None = None, timeout_s: float = 10) -> dict[str, Any]:
        remaining = _remaining_timeout(deadline, timeout_s)
        describe = self.observations.describe
        try:
            supports_timeout = "timeout_s" in inspect.signature(describe).parameters
        except (TypeError, ValueError):
            supports_timeout = False
        if supports_timeout:
            catalog = describe(timeout_s=remaining)
        else:
            catalog = describe()
        _remaining_timeout(deadline, timeout_s)
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
        snap = self._document_snapshot(chunk.snapshot_sha256, chunk.source_id, chunk.doc_key, warnings)
        data = {
            "status": "ok", "chunk_id": chunk_id, "doc_key": chunk.doc_key,
            "snapshot_sha256": chunk.snapshot_sha256, "chunks": views, "law": law,
        }
        freshness = _freshness([(snap.source_id, snap.fetched_at)] if snap else [])
        return _result(data, GapState.ok, freshness, warnings)

    # ----- cite -----

    def cite(
        self, snapshot_sha256: str, locator: str, snapshot_id: str | None = None, resource_url: str | None = None,
    ) -> dict[str, Any]:
        """回到 raw：取快照原始字节、校验哈希、用对应解析器重新解析，按 locator 找回原文。

        snapshot_id 精确指定观察；resource_url 限定资源。SHA 跨资源时返回 ambiguous 和候选。
        定位不到时 ``data.status = "not_found"``，原因在 gap_detail。
        """
        if not isinstance(snapshot_sha256, str) or not _SHA_RE.fullmatch(snapshot_sha256):
            raise ValueError("snapshot_sha256 必须是 64 位小写十六进制")
        if not isinstance(locator, str):
            raise ValueError("locator 必须是字符串")
        locator_parts = parse_locator(locator)

        def not_found(reason: str) -> dict[str, Any]:
            data = {"status": "not_found", "snapshot_sha256": snapshot_sha256, "locator": locator, "passages": []}
            return _result(data, GapState.zero_hits, gap_detail=reason)

        for name, value in (("snapshot_id", snapshot_id), ("resource_url", resource_url)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} 必须是非空字符串")
        try:
            snap = self._snapshot(snapshot_sha256, snapshot_id=snapshot_id, resource_url=resource_url)
        except AmbiguousCitation as exc:
            candidates = [
                {"snapshot_id": s.snapshot_id, "source_id": s.source_id, "resource_url": s.resource_url}
                for s in self.manifest.snapshots_by_sha256(snapshot_sha256)
                if s.outcome is FetchOutcome.ok
                and (resource_url is None or s.resource_url == redact_url(resource_url))
            ]
            return _result(
                {"status": "ambiguous", "snapshot_sha256": snapshot_sha256, "locator": locator,
                 "passages": [], "candidates": candidates},
                GapState.zero_hits, warnings=[str(exc)], gap_detail=str(exc),
            )
        if snap is None:
            return not_found("manifest 里没有内容是这个 sha256 的成功抓取")
        try:
            self.raw.verify(snapshot_sha256)
            raw = self.raw.get(snapshot_sha256)
        except NotFoundError:
            return not_found("raw 里没有这个快照的原始字节")
        except CorruptedBlob:
            return not_found("raw 里的内容和 sha256 不一致，不能用来还原")
        entry_spec = self.registry.get(snap.source_id) if "entry" in locator_parts else None
        if "entry" in locator_parts and entry_spec is not None and entry_spec.kind == "ckan":
            if set(locator_parts) != {"entry"}:
                return not_found("CKAN package_search 的 entry locator 不能包含其他段")
            raw_index = locator_parts["entry"]
            if re.fullmatch(r"[0-9]+", raw_index) is None:
                return not_found("entry locator 必须是非负整数")
            try:
                entry_index = int(raw_index)
            except ValueError:
                return not_found("entry locator 必须是非负整数")
            action = urlsplit(snap.resource_url).path.rstrip("/").rsplit("/", 1)[-1]
            if action != "package_search":
                return not_found("entry locator 只适用于 CKAN package_search 快照")
            try:
                text = raw.decode("utf-8")
                payload = json.loads(text)
            except (UnicodeDecodeError, json.JSONDecodeError):
                return not_found("CKAN package_search 快照不是有效的 UTF-8 JSON")
            page = payload.get("result") if isinstance(payload, dict) else None
            records = page.get("results") if isinstance(page, dict) else None
            if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(records, list):
                return not_found("快照不是有效的 CKAN package_search JSON")
            if entry_index >= len(records):
                return not_found(f"entry 下标 {entry_index} 超出 results 数组范围（共 {len(records)} 条）")
            try:
                result_span = _json_object_value_span(text, 0, "result")
                records_span = None if result_span is None else _json_object_value_span(text, result_span[0], "results")
                entry_span = None if records_span is None else _json_array_item_span(text, records_span[0], entry_index)
            except (ValueError, json.JSONDecodeError):
                entry_span = None
            if entry_span is None or not isinstance(records[entry_index], dict):
                return not_found("无法从 CKAN package_search 原文定位该目录记录")
            passage = {"kind": "catalog_entry", "text": SourceText(content=text[entry_span[0]:entry_span[1]])}
            data = {"status": "ok", "snapshot_sha256": snapshot_sha256, "locator": locator, "passages": [passage]}
            return _result(data, GapState.ok, _freshness([(snap.source_id, snap.fetched_at)]))
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
        """注册表里的全部来源：抓取状态、管线覆盖、新鲜度、失败统计和许可。"""
        rows = []
        freshness = []
        warnings: list[str] = []
        generation = index_generation(self.retriever, data_dir=self.data_dir)
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
                "pipeline": pipeline_status(
                    [spec.id], registry=self.registry, manifest=self.manifest, chunks=self.chunks,
                    parsers=self._parsers(), index_generation=generation,
                ),
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
            empty = pipeline_status(
                [], registry=self.registry, manifest=self.manifest, chunks=self.chunks,
                parsers=self._parsers(), index_generation=generation,
            )
            return _result(
                {"sources": []}, GapState.zero_hits, gap_detail="注册表里没有任何来源", pipeline=empty,
            )
        overall = pipeline_status(
            None, registry=self.registry, manifest=self.manifest, chunks=self.chunks,
            parsers=self._parsers(), index_generation=generation,
        )
        return _result(
            {"sources": rows}, GapState.ok, _merge_freshness(freshness), warnings, pipeline=overall,
        )
