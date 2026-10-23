"""关键词、向量与多个查询的 RRF 融合检索。"""

from collections.abc import Sequence

from datumoj.core.citations import document_snapshot
from datumoj.core.contracts import (
    AmbiguousCitation,
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
from datumoj.core.models import Freshness, GapState, Hit, MatchedBy, SourceText, ToolResult
from datumoj.index.coverage import gap_state


# 向量相似度阈值依嵌入模型而定：不同模型的分数分布差别很大。
# bge-m3 由 H5（2026-10-21）在 60 题评测语料上校准：无关问题的向量最高分 0.471，相关检索题最低 0.685，
# 取 0.6 两边各留约 0.1 余量。详见 docs/adr/0001-embedding.md。其他模型（含测试用的 HashEmbedder）用保守的 0.3。
MIN_VECTOR_SCORE_BY_MODEL = {"bge-m3": 0.6}
DEFAULT_MIN_VECTOR_SCORE = 0.3


def min_vector_score_for(vector: VectorIndex | None) -> float:
    """按向量索引所用的嵌入模型选阈值；模型名带标签（如 bge-m3:latest）时按冒号前的名字匹配。"""
    model = getattr(getattr(vector, "embedder", None), "model", None)
    if isinstance(model, str):
        return MIN_VECTOR_SCORE_BY_MODEL.get(model.split(":", 1)[0], DEFAULT_MIN_VECTOR_SCORE)
    return DEFAULT_MIN_VECTOR_SCORE


class HybridRetriever:
    def __init__(
        self,
        chunks: ChunkStore,
        manifest: Manifest,
        registry: Registry,
        keyword: KeywordIndex,
        vector: VectorIndex | None = None,
        min_vector_score: float | None = None,
    ) -> None:
        self.chunks = chunks
        self.manifest = manifest
        self.registry = registry
        self.keyword = keyword
        self.vector = vector
        self.min_vector_score = min_vector_score_for(vector) if min_vector_score is None else min_vector_score

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
        vector_results_ignored = False
        for method, results in ((MatchedBy.keyword, keyword_results), (MatchedBy.vector, vector_results)):
            for result in results:
                if method is MatchedBy.vector:
                    items = [item for item in result.items if item.score >= self.min_vector_score]
                    if len(items) != len(result.items):
                        vector_results_ignored = True
                    result = result.model_copy(update={"items": items})
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
            try:
                snapshot = document_snapshot(
                    self.manifest, chunk.snapshot_sha256, chunk.source_id, chunk.doc_key,
                    self.chunks.get_document(chunk.doc_key, chunk.snapshot_sha256),
                )
            except AmbiguousCitation:
                warnings.append(f"{chunk_id} 来源快照有歧义，无法确定资源 URL，已跳过")
                continue
            if snapshot is None:
                warnings.append(f"{chunk_id} 找不到来源快照，已跳过")
                continue
            spec = self.registry.get(chunk.source_id)
            hits.append(Hit(
                chunk_id=chunk_id, doc_key=chunk.doc_key, locator=chunk.locator, score=scores[chunk_id],
                snippet=SourceText(content=chunk.text[:300]),
                citation=citation_for_chunk(chunk, snapshot, spec.license if spec else None),
                matched_by=matched[chunk_id],
            ))

        if vector_results_ignored and not hits:
            warnings.append("向量近邻相似度过低，已忽略")

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
