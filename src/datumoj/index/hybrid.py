"""关键词、向量与多个查询的 RRF 融合检索。"""

from collections.abc import Sequence

from datumoj.core.contracts import (
    ChunkStore,
    DateRange,
    EmbeddingError,
    IndexResult,
    KeywordIndex,
    Manifest,
    ModelMismatch,
    Registry,
    VectorIndex,
    citation_for_chunk,
)
from datumoj.core.models import FetchOutcome, Freshness, GapState, Hit, MatchedBy, SourceText, ToolResult
from datumoj.index.coverage import gap_state


class HybridRetriever:
    def __init__(
        self,
        chunks: ChunkStore,
        manifest: Manifest,
        registry: Registry,
        keyword: KeywordIndex,
        vector: VectorIndex | None = None,
    ) -> None:
        self.chunks = chunks
        self.manifest = manifest
        self.registry = registry
        self.keyword = keyword
        self.vector = vector

    def search(
        self,
        queries: Sequence[str],
        k: int = 10,
        source_ids: Sequence[str] | None = None,
        date_range: DateRange | None = None,
    ) -> ToolResult[list[Hit]]:
        return self.hybrid_search(queries, k, source_ids, date_range)

    def hybrid_search(
        self,
        queries: Sequence[str],
        k: int = 10,
        source_ids: Sequence[str] | None = None,
        date_range: DateRange | None = None,
    ) -> ToolResult[list[Hit]]:
        qs = [q for q in queries if isinstance(q, str) and q.strip()]
        if not qs or k < 1:
            raise ValueError("queries 不能为空白，k 必须 ≥ 1")
        if date_range is not None:
            lo, hi = date_range
            if lo is not None and hi is not None and lo > hi:
                raise ValueError("date_range 起日不能晚于止日")

        warnings = []
        keyword_results = []
        vector_results = []
        vector_available = self.vector is not None
        if not vector_available:
            warnings.append("degraded=true：向量索引未配置，退回纯关键词")
        for query in qs:
            result = self.keyword.search(query, k=k, source_ids=source_ids)
            keyword_results.append(result)
            warnings.extend(result.warnings)
            if vector_available:
                try:
                    result = self.vector.search(query, k=k, source_ids=source_ids)
                except (EmbeddingError, ModelMismatch, OSError, RuntimeError) as exc:
                    # 一条子查询失败即舍弃本轮全部向量结果，保持纯关键词的排序。
                    vector_available = False
                    vector_results.clear()
                    warnings.append(f"degraded=true：向量检索不可用（{type(exc).__name__}），退回纯关键词")
                else:
                    vector_results.append(result)
                    warnings.extend(result.warnings)

        scores: dict[str, float] = {}
        matched: dict[str, list[MatchedBy]] = {}
        for method, results in ((MatchedBy.keyword, keyword_results), (MatchedBy.vector, vector_results)):
            for result in results:
                self._fuse(result, method, scores, matched)

        hits = []
        for chunk_id in sorted(scores, key=lambda cid: (-scores[cid], cid)):
            if len(hits) >= k:
                break
            chunk = self.chunks.get_chunk(chunk_id)
            if chunk is None:
                warnings.append(f"{chunk_id} 找不到当前块，已跳过")
                continue
            if source_ids is not None and chunk.source_id not in source_ids:
                continue
            if date_range is not None:
                if not any(
                    d is not None and (lo is None or d >= lo) and (hi is None or d <= hi)
                    for d in (chunk.published_at, chunk.effective_at)
                ):
                    continue
            snapshots = [
                s for s in self.manifest.snapshots_by_sha256(chunk.snapshot_sha256)
                if s.source_id == chunk.source_id and s.outcome is FetchOutcome.ok
            ]
            if not snapshots:
                warnings.append(f"{chunk_id} 找不到来源快照，已跳过")
                continue
            spec = self.registry.get(chunk.source_id)
            hits.append(Hit(
                chunk_id=chunk_id, doc_key=chunk.doc_key, locator=chunk.locator, score=scores[chunk_id],
                snippet=SourceText(content=chunk.text[:300]),
                citation=citation_for_chunk(chunk, snapshots[-1], spec.license if spec else None),
                matched_by=matched[chunk_id],
            ))

        times = [h.citation.fetched_at for h in hits]
        by_source = {}
        for hit in hits:
            citation = hit.citation
            by_source[citation.source_id] = max(
                by_source.get(citation.source_id, citation.fetched_at), citation.fetched_at,
            )
        freshness = Freshness(
            as_of=max(times, default=None), oldest_fetched_at=min(times, default=None), by_source=by_source,
        )
        state, detail = self.gap_state(source_ids, hits)
        return ToolResult[list[Hit]](
            data=hits, gap_state=state, gap_detail=detail, freshness=freshness,
            warnings=list(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _fuse(
        result: IndexResult,
        method: MatchedBy,
        scores: dict[str, float],
        matched: dict[str, list[MatchedBy]],
    ) -> None:
        seen = set()
        for rank, item in enumerate(result.items, 1):
            if item.chunk_id in seen:
                continue
            seen.add(item.chunk_id)
            scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + 1 / (60 + rank)
            methods = matched.setdefault(item.chunk_id, [])
            if method not in methods:
                methods.append(method)

    def gap_state(self, source_ids: Sequence[str] | None, hits: Sequence[Hit]) -> tuple[GapState, str | None]:
        return gap_state(source_ids, hits, registry=self.registry, manifest=self.manifest)
