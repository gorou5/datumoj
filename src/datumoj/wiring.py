"""把已实现的协议接到本地数据目录；未安装的组件按能力报告。"""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from datumoj.core.contracts import ParseError, ParseResult, discover_parsers
from datumoj.parsers.ckan_meta import CkanMetaParser
from datumoj.store.catalog import SqliteCatalogStore


class _CkanPages(CkanMetaParser):
    """把采集器的分页逐项交给现有 package 解析器；资源文件不当作目录 JSON。"""

    name = "ckan_pages"
    version = "1"

    def parse(self, snapshot, data, meta=None):
        if meta and meta.get("role") in {"resource", "activity"}:
            return ParseResult()
        try:
            payload = json.loads(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ParseError("CKAN 元数据不是有效 JSON") from exc
        page = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(page, dict) or "results" not in page:
            return super().parse(snapshot, data, meta)
        if payload.get("success") is not True or not isinstance(page["results"], list):
            raise ParseError("CKAN package_search 响应无效")
        datasets, resources, warnings = [], [], []
        for package in page["results"]:
            parsed = super().parse(snapshot, json.dumps({"result": package}).encode(), meta)
            datasets.extend(parsed.datasets)
            resources.extend(parsed.resources)
            warnings.extend(parsed.warnings)
        return ParseResult(datasets=datasets, resources=resources, warnings=warnings)


def _parse_pending(deps, source_id=None):
    from datumoj.pipeline import parse_pending

    if deps.parsers is None:
        parsers = discover_parsers()
        parsers[("ckan", "*")] = _CkanPages
        deps = replace(deps, parsers=parsers)
    return parse_pending(deps, source_id)


class _CatalogQueries(SqliteCatalogStore):
    """目录库接受单个词项；在入口把空格分隔的多词查询做 RRF 合并。"""

    def search(self, query, filters=None, k=20):
        terms = list(dict.fromkeys(query.split()))
        if len(terms) <= 1:
            return super().search(query, filters, k)
        hits, scores = {}, {}
        for term in terms:
            for rank, hit in enumerate(super().search(term, filters, k), 1):
                key = hit.dataset.dataset_key
                hits[key] = hit
                scores[key] = scores.get(key, 0.0) + 1 / (60 + rank)
        keys = sorted(hits, key=lambda key: (-scores[key], key))[:k]
        return [hits[key].model_copy(update={"score": scores[key]}) for key in keys]


class FeatureNotConnected(RuntimeError):
    """请求的功能缺少实现。"""


@dataclass
class Wiring:
    data_dir: Path
    registry: Any = None
    raw: Any = None
    manifest: Any = None
    chunks: Any = None
    keyword: Any = None
    vectors: Any = None
    catalog: Any = None
    observations: Any = None
    retriever: Any = None
    tools: Any = None
    pipeline_deps: Any = None
    crawler_classes: dict[str, type] = field(default_factory=dict)
    fetcher_factory: Any = None
    parse_pending: Any = None
    reindex: Any = None
    run_crawl: Any = None
    crawl_deps_type: Any = None
    unavailable: dict[str, str] = field(default_factory=dict)

    def require(self, feature: str) -> None:
        if feature in self.unavailable:
            raise FeatureNotConnected(self.unavailable[feature])

    def fetcher_for(self, spec: Any) -> Any:
        self.require("crawl")
        factory = self.fetcher_factory
        if hasattr(factory, "from_spec"):
            return factory.from_spec(spec)
        return factory


def _import(module_name: str, symbol: str) -> Any:
    module = importlib.import_module(module_name)
    return getattr(module, symbol)


def _get(overrides: dict[str, Any], key: str, module: str, symbol: str, *args: Any) -> Any:
    if key in overrides:
        return overrides[key]
    try:
        implementation = _import(module, symbol)
    except ImportError:
        return None
    return implementation(*args)


def build(data_dir: str | Path, overrides: dict[str, Any] | None = None) -> Wiring:
    """按协议组装依赖。``overrides`` 可替换任意命名依赖，供测试和嵌入式调用注入。"""
    overrides = dict(overrides or {})
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    wiring = Wiring(data_dir=root)

    wiring.raw = _get(overrides, "raw", "datumoj.core.rawstore", "FileRawStore", root)
    wiring.manifest = _get(
        overrides, "manifest", "datumoj.core.manifest", "SqliteManifest", root / "manifest.sqlite"
    )
    wiring.chunks = _get(overrides, "chunks", "datumoj.store.chunks", "SqliteChunkStore", root / "chunks.sqlite")
    wiring.keyword = _get(
        overrides, "keyword", "datumoj.index.fts", "SqliteKeywordIndex", root / "keyword.sqlite"
    )
    wiring.catalog = _get(
        overrides, "catalog", "datumoj.wiring", "_CatalogQueries", root / "catalog.sqlite"
    )
    wiring.observations = _get(
        overrides, "observations", "datumoj.store.observations", "DuckDBObservationStore", root
    )

    if "registry" in overrides:
        wiring.registry = overrides["registry"]
    else:
        try:
            load_registry = _import("datumoj.core.registry", "load_registry")
        except ImportError:
            wiring.registry = None
        else:
            wiring.registry = load_registry(Path(__file__).resolve().parents[2] / "sources")

    # Vector search is optional; keyword search remains usable when LanceDB is absent.
    vectors_off = os.environ.get("DATUMOJ_VECTORS", "").strip().lower() == "off"
    if vectors_off:
        wiring.vectors = None
    elif "vectors" in overrides:
        wiring.vectors = overrides["vectors"]
    elif wiring.chunks is not None:
        try:
            embedder_type = _import("datumoj.index.embed", "OllamaEmbedder")
            vector_type = _import("datumoj.index.vector", "LanceVectorIndex")
        except ImportError:
            wiring.vectors = None
        else:
            wiring.vectors = vector_type(root / "vectors", embedder_type())

    if all((wiring.chunks, wiring.manifest, wiring.registry, wiring.keyword)):
        wiring.retriever = overrides.get("retriever")
        if wiring.retriever is None:
            try:
                retriever_type = _import("datumoj.index.hybrid", "HybridRetriever")
            except ImportError:
                pass
            else:
                wiring.retriever = retriever_type(
                    wiring.chunks, wiring.manifest, wiring.registry, wiring.keyword, wiring.vectors
                )

    if all((wiring.raw, wiring.manifest, wiring.registry, wiring.chunks, wiring.keyword,
            wiring.observations, wiring.catalog)):
        try:
            deps_type = _import("datumoj.pipeline", "PipelineDeps")
            wiring.parse_pending = overrides.get("parse_pending") or _parse_pending
            wiring.reindex = overrides.get("reindex") or _import("datumoj.pipeline", "reindex")
        except ImportError:
            pass
        else:
            wiring.pipeline_deps = overrides.get("pipeline_deps") or deps_type(
                manifest=wiring.manifest, raw=wiring.raw, registry=wiring.registry,
                chunks=wiring.chunks, keyword=wiring.keyword, observations=wiring.observations,
                catalog=wiring.catalog, vectors=wiring.vectors, parsers=overrides.get("parsers"),
                vector_state_path=root / "vectors-state.json",
            )

    if all((wiring.retriever, wiring.catalog, wiring.observations, wiring.chunks, wiring.manifest,
            wiring.raw, wiring.registry)):
        wiring.tools = overrides.get("tools")
        if wiring.tools is None:
            try:
                tools_type = _import("datumoj.analysis.tools", "AnalysisTools")
            except ImportError:
                pass
            else:
                wiring.tools = tools_type(
                    retriever=wiring.retriever, catalog=wiring.catalog, observations=wiring.observations,
                    chunks=wiring.chunks, manifest=wiring.manifest, raw=wiring.raw, registry=wiring.registry,
                    parsers=overrides.get("parsers"), data_dir=root,
                )

    wiring.crawler_classes = overrides.get("crawlers", {})
    if not wiring.crawler_classes:
        try:
            wiring.crawler_classes = _import("datumoj.core.contracts", "discover_crawlers")(
                skip_broken=True
            )
        except ImportError:
            wiring.crawler_classes = {}
    wiring.fetcher_factory = overrides.get("fetcher")
    if wiring.fetcher_factory is None:
        try:
            wiring.fetcher_factory = _import("datumoj.core.http", "PoliteHttpFetcher")
        except ImportError:
            wiring.fetcher_factory = None
    try:
        wiring.run_crawl = overrides.get("run_crawl") or _import("datumoj.core.runner", "run_crawl")
        wiring.crawl_deps_type = overrides.get("crawl_deps_type") or _import("datumoj.core.runner", "CrawlDeps")
    except ImportError:
        pass

    if "tools" in overrides:
        wiring.tools = overrides["tools"]
    for feature, available in (
        ("crawl", all((wiring.registry, wiring.raw, wiring.manifest, wiring.fetcher_factory,
                        wiring.run_crawl, wiring.crawl_deps_type))),
        ("parse", all((wiring.pipeline_deps, wiring.parse_pending))),
        ("index", all((wiring.pipeline_deps, wiring.parse_pending, wiring.reindex))),
        ("search", all((wiring.tools, wiring.chunks, wiring.registry))),
        ("sql", bool(wiring.tools)),
        ("status", all((wiring.tools, wiring.registry))),
    ):
        if not available:
            wiring.unavailable[feature] = "该功能尚未接入"
    return wiring
