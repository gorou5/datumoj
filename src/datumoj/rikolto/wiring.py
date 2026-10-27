"""把已实现的协议接到本地数据目录；未安装的组件按能力报告。"""

from __future__ import annotations

import importlib
import json
import os
import uuid
from dataclasses import dataclass, field, replace
from functools import wraps
from pathlib import Path
from typing import Any

from datumoj.rikolto.core.contracts import ParseError, Parser, ParseResult, discover_parsers
from datumoj.rikolto.core.storage import load_storage_config
from datumoj.rikolto.core.state import StateManager as _StateManager, local_write
from datumoj.rikolto.parsers.ckan_meta import CkanMetaParser
from datumoj.rikolto.store.catalog import SqliteCatalogStore


class _CkanPages(CkanMetaParser):
    """把采集器的分页逐项交给现有 package 解析器；资源文件不当作目录 JSON。"""

    name = "ckan_pages"
    version = "2"

    def parse(self, snapshot, data, meta=None):
        if meta and meta.get("role") in {"resource", "activity", "datastore"}:
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
        for index, package in enumerate(page["results"]):
            if not isinstance(package, dict):
                warnings.append(f"results[{index}] 不是对象，已跳过")
                continue
            parsed = self._parse_package(snapshot, package, locator=f"entry={index}")
            datasets.extend(parsed.datasets)
            resources.extend(parsed.resources)
            warnings.extend(parsed.warnings)
        return ParseResult(datasets=datasets, resources=resources, warnings=warnings)


class _GovWebContentVersion:
    def __get__(self, instance, owner) -> str:
        from datumoj.rikolto.parsers.html_page import HtmlPageParser
        from datumoj.rikolto.parsers.pdf import PdfParser

        return f"pdf-{PdfParser.version}+html-{HtmlPageParser.version}"


class _GovWebContent(Parser):
    """网页附件共用分派入口；按正文选择已有实现，避免错误响应头选错解析器。"""

    name = "gov-web-content"
    version = _GovWebContentVersion()
    source_kind = "gov_web"
    content_type = "*"

    def parse(self, snapshot, data, meta=None):
        from datumoj.rikolto.parsers.html_page import HtmlPageParser
        from datumoj.rikolto.parsers.pdf import PdfParser

        parser = PdfParser() if data.lstrip().startswith(b"%PDF-") else HtmlPageParser()
        return parser.parse(snapshot, data, meta)


def _connected_parsers(*, skip_broken=False):
    parsers = discover_parsers(skip_broken=skip_broken)
    parsers[("ckan", "*")] = _CkanPages
    for media in ("text/html", "application/pdf", "*"):
        parsers[("gov_web", media)] = _GovWebContent
    return parsers


def _parse_pending(deps, source_id=None):
    from datumoj.rikolto.pipeline import parse_pending

    if deps.parsers is None:
        deps = replace(deps, parsers=_connected_parsers())
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
    change_log: Any = None

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


def build(data_dir: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Wiring:
    # Store constructors can initialize/migrate schemas, so they share the same
    # boundary as complete crawl/parse/index operations and state snapshots.
    root = load_storage_config(data_dir=data_dir).data_dir
    with local_write(root):
        return _build(data_dir, overrides)


def _build(data_dir: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Wiring:
    """按协议组装依赖。``overrides`` 可替换任意命名依赖，供测试和嵌入式调用注入。"""
    overrides = dict(overrides or {})
    config = load_storage_config(data_dir=data_dir)
    root = config.data_dir
    restored = root / "state-storage.json"
    if restored.exists():
        from datumoj.rikolto.core.storage import StorageConfig

        value = json.loads(restored.read_bytes())
        if value not in ({"raw_backend": "local"}, {"raw_backend": "s3"}):
            raise ValueError("恢复目录的 raw 配置无效")
        config = StorageConfig.model_validate({**config.model_dump(), **value})
    root.mkdir(parents=True, exist_ok=True)
    wiring = Wiring(data_dir=root)

    if config.raw_backend == "s3":
        wiring.raw = _get(overrides, "raw", "datumoj.rikolto.core.rawstore_s3", "S3RawStore", config)
    else:
        wiring.raw = _get(overrides, "raw", "datumoj.rikolto.core.rawstore", "FileRawStore", root)
    wiring.manifest = _get(
        overrides, "manifest", "datumoj.rikolto.core.manifest", "SqliteManifest", root / "manifest.sqlite"
    )
    wiring.chunks = _get(overrides, "chunks", "datumoj.rikolto.store.chunks", "SqliteChunkStore", root / "chunks.sqlite")
    wiring.keyword = _get(
        overrides, "keyword", "datumoj.rikolto.index.fts", "SqliteKeywordIndex", root / "keyword.sqlite"
    )
    wiring.catalog = _get(
        overrides, "catalog", "datumoj.rikolto.wiring", "_CatalogQueries", root / "catalog.sqlite"
    )
    wiring.observations = _get(
        overrides, "observations", "datumoj.rikolto.store.observations", "DuckDBObservationStore", root
    )
    wiring.change_log = _get(overrides, "change_log", "datumoj.rikolto.store.changes", "SqliteChangeLog", root / "changes.sqlite")

    if "registry" in overrides:
        wiring.registry = overrides["registry"]
    else:
        try:
            load_registry = _import("datumoj.rikolto.core.registry", "load_registry")
        except ImportError:
            wiring.registry = None
        else:
            wiring.registry = load_registry(Path(__file__).resolve().parents[3] / "sources")

    # Vector search is optional; keyword search remains usable when LanceDB is absent.
    vectors_off = os.environ.get("DATUMOJ_VECTORS", "").strip().lower() == "off"
    if vectors_off:
        wiring.vectors = None
    elif "vectors" in overrides:
        wiring.vectors = overrides["vectors"]
    elif wiring.chunks is not None:
        try:
            vector_type = _import("datumoj.rikolto.index.vector", "LanceVectorIndex")
            embedder = overrides.get("embedder")
            if embedder is None:
                embedder = _import("datumoj.rikolto.index.embed", "OllamaEmbedder")()
        except ImportError:
            wiring.vectors = None
        else:
            wiring.vectors = vector_type(root / "vectors", embedder)

    if all((wiring.chunks, wiring.manifest, wiring.registry, wiring.keyword)):
        wiring.retriever = overrides.get("retriever")
        if wiring.retriever is None:
            try:
                retriever_type = _import("datumoj.rikolto.index.hybrid", "HybridRetriever")
            except ImportError:
                pass
            else:
                wiring.retriever = retriever_type(
                    wiring.chunks, wiring.manifest, wiring.registry, wiring.keyword, wiring.vectors
                )

    if all((wiring.raw, wiring.manifest, wiring.registry, wiring.chunks, wiring.keyword,
            wiring.observations, wiring.catalog)):
        try:
            deps_type = _import("datumoj.rikolto.pipeline", "PipelineDeps")
            wiring.parse_pending = overrides.get("parse_pending") or _parse_pending
            wiring.reindex = overrides.get("reindex") or _import("datumoj.rikolto.pipeline", "reindex")
        except ImportError:
            pass
        else:
            wiring.pipeline_deps = overrides.get("pipeline_deps") or deps_type(
                manifest=wiring.manifest, raw=wiring.raw, registry=wiring.registry,
                chunks=wiring.chunks, keyword=wiring.keyword, observations=wiring.observations,
                catalog=wiring.catalog, vectors=wiring.vectors, parsers=overrides.get("parsers"),
                vector_state_path=root / "vectors-state.json",
                change_log=wiring.change_log,
            )

    if all((wiring.retriever, wiring.catalog, wiring.observations, wiring.chunks, wiring.manifest,
            wiring.raw, wiring.registry)):
        wiring.tools = overrides.get("tools")
        if wiring.tools is None:
            try:
                tools_type = _import("datumoj.rikolto.analysis.tools", "AnalysisTools")
            except ImportError:
                pass
            else:
                wiring.tools = tools_type(
                    retriever=wiring.retriever, catalog=wiring.catalog, observations=wiring.observations,
                    chunks=wiring.chunks, manifest=wiring.manifest, raw=wiring.raw, registry=wiring.registry,
                    parsers=(_connected_parsers(skip_broken=True) if overrides.get("parsers") is None
                             else overrides["parsers"]),
                    data_dir=root,
                    change_log=wiring.change_log,
                )

    wiring.crawler_classes = overrides.get("crawlers", {})
    if not wiring.crawler_classes:
        try:
            wiring.crawler_classes = _import("datumoj.rikolto.core.contracts", "discover_crawlers")(
                skip_broken=True
            )
        except ImportError:
            wiring.crawler_classes = {}
    wiring.fetcher_factory = overrides.get("fetcher")
    if wiring.fetcher_factory is None:
        try:
            wiring.fetcher_factory = _import("datumoj.rikolto.core.http", "PoliteHttpFetcher")
        except ImportError:
            wiring.fetcher_factory = None
    try:
        wiring.run_crawl = overrides.get("run_crawl") or _import("datumoj.rikolto.core.runner", "run_crawl")
        wiring.crawl_deps_type = overrides.get("crawl_deps_type") or _import("datumoj.rikolto.core.runner", "CrawlDeps")
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
    def guarded(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with local_write(root):
                return function(*args, **kwargs)
        return invoke

    for name in ("run_crawl", "parse_pending", "reindex"):
        function = getattr(wiring, name)
        if function is not None:
            setattr(wiring, name, guarded(function))
    return wiring


def build_state(data_dir: str | Path | None = None):
    """State commands avoid opening or migrating live data stores."""
    from datumoj.rikolto.core.models import StateWriter
    from datumoj.rikolto.core.state_s3 import S3StateObjectStore

    config = load_storage_config(data_dir=data_dir)
    run_id = os.environ.get("GITHUB_RUN_ID")
    kind = state_writer_kind()
    writer = StateWriter(kind=kind, run_id=run_id or uuid.uuid4().hex)
    return _ReleaseStateManager(S3StateObjectStore(config), config.data_dir, writer=writer)


def state_writer_kind() -> str:
    """Return the configured writer kind, preserving the legacy Actions fallback."""
    configured = os.environ.get("DATUMOJ_WRITER_KIND")
    if configured is not None:
        if configured not in {"actions", "local", "nas"}:
            raise ValueError("DATUMOJ_WRITER_KIND 必须是 actions、local 或 nas")
        return configured
    return "actions" if os.environ.get("GITHUB_RUN_ID") else "local"


def build_tools() -> Any:
    """MCP 的无参工厂：在服务器工作线程里创建存储及工具层。"""
    deps = build(str(load_storage_config().data_dir))
    deps.require("search")
    return deps.tools


# The state implementation remains unchanged. These composition hooks retain the
# exact verified publication boundary for observation queries.
class _ReleaseStateManager(_StateManager):
    def restore(self, target, generation=None, *, raw="local"):
        report = super().restore(target, generation, raw=raw)
        if raw == "local":
            self.retain(target, report["generation"])
        return report

    def retain(self, target, generation):
        from datumoj.rikolto.store.observations import retain_generation

        if not any(g.generation == generation for g in self.list()):
            raise ValueError("代次不在有效发布链中")
        found = self.store.get(f"generations/{generation}.json")
        if found is None:
            raise ValueError("已发布清单不可用")
        return retain_generation(Path(target), found[0], Path(target), verified=True)

    def push(self):
        import tempfile
        from datumoj.rikolto.store.observations import retain_generation

        manifest = super().push()
        # Restore the published copy, never recopy mutable live current after CAS.
        with tempfile.TemporaryDirectory(prefix=".retain-push-", dir=self.data_dir) as temp:
            target = Path(temp) / "published"
            super().restore(target, manifest.generation, raw="local")
            found = self.store.get(f"generations/{manifest.generation}.json")
            if found is None:
                raise ValueError("已发布清单不可用")
            retain_generation(self.data_dir, found[0], target, verified=True, immutable_root=self.data_dir)
        return manifest


def retain_pulled_generation(root, generation):
    """Called after the pull implementation has verified and installed all files."""
    import shutil

    root = Path(root)
    previous = root.with_name(root.name + ".previous") / ".observation-generations"
    if previous.exists():
        from datumoj.rikolto.store.observations import _link_or_copy

        shutil.copytree(previous, root / ".observation-generations", dirs_exist_ok=True, copy_function=_link_or_copy)
    return build_state(root).retain(root, generation)


def import_observation_release(deps, source_id, file, release_id, released_at):
    """Match local bytes against registered source plans, without fetching URLs."""
    from datetime import UTC, date, datetime
    from itertools import islice
    from datumoj.rikolto.core.contracts import CrawlContext, ParseError, select_parser
    from datumoj.rikolto.core.models import FetchOutcome, PublishState, Snapshot

    if not isinstance(release_id, str) or not release_id.strip() or type(released_at) is not date:
        raise ValueError("必须显式给出发布标识和来源发布日期")
    spec = deps.registry.get(source_id)
    if spec is None:
        raise ValueError("来源未注册")
    with Path(file).open('rb') as stream:
        data = stream.read(spec.max_total_bytes + 1)
    if len(data) > spec.max_total_bytes:
        raise ValueError("文件超过来源 max_total_bytes 上限")
    sha = deps.raw.put(data)
    now = datetime.now(UTC)
    crawler = deps.crawler_classes.get(spec.kind)
    if crawler is None:
        raise ValueError("来源没有已注册的采集计划，无法确认文件出处")
    parsers = _connected_parsers()
    matches = []
    ctx = CrawlContext(spec=spec, manifest=deps.manifest, raw=deps.raw, now=now, limit=spec.max_files)
    for task in islice(crawler().plan(ctx), spec.max_files):
        # The source's existing plan supplies official URL and parser metadata.
        # Accept exactly one successful match; filenames never determine identity.
        candidates = {cls for (kind, _), cls in parsers.items() if kind == spec.kind}
        preferred = select_parser(parsers, spec.kind, 'application/octet-stream')
        if preferred is not None:
            candidates = {preferred}
        for cls in candidates:
            snapshot = Snapshot(snapshot_id=uuid.uuid4().hex, source_id=source_id,
                                resource_url=task.redacted_url, fetched_at=now,
                                outcome=FetchOutcome.ok, sha256=sha, size=len(data),
                                content_type=None if cls.content_type == '*' else cls.content_type)
            try:
                parsed = cls().parse(snapshot, data, task.meta)
                parsed.check_against(snapshot)
            except ParseError:
                continue
            if parsed.observations:
                matches.append((snapshot, task.meta, parsed))
    if len(matches) != 1:
        raise ValueError("文件必须唯一匹配来源现有解析计划；无法确认出处或存在歧义")
    snapshot, meta, parsed = matches[0]
    deps.manifest.record_snapshot(snapshot, meta)
    order = deps.manifest.snapshot_order(snapshot.snapshot_id)
    if order is None:
        raise ValueError("导入快照缺少持久化 SnapshotOrder")
    tables = {}
    for obs in parsed.observations:
        labelled = obs.model_copy(update={'release_id': release_id, 'released_at': released_at})
        tables.setdefault(obs.table_key, []).append(labelled)
    deps.manifest.record_publish_state(snapshot.snapshot_id, PublishState.staged)
    for table_key, observations in tables.items():
        deps.observations.import_release(source_id, table_key, observations, order)
    deps.manifest.record_publish_state(snapshot.snapshot_id, PublishState.published)
    return {'snapshot_id': snapshot.snapshot_id, 'snapshot_sha256': sha, 'release_id': release_id,
            'released_at': released_at.isoformat(), 'observations': len(parsed.observations),
            'warnings': parsed.warnings}
