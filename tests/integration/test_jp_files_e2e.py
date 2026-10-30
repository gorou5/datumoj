"""Independent source acceptance using recorded bytes and the actual pipeline."""

import hashlib
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.contracts import CrawlContext
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import Citation, TimeBasis, parse_locator
from datumoj.rikolto.core.registry import RegistryImpl, load_registry
from datumoj.rikolto.core.runner import CrawlDeps, run_crawl
from datumoj.rikolto.core.schedule import read_control
from datumoj.rikolto.testing.samples import T0

ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Government HTTP must use ReplayTransport")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")


def restore(deps, citation):
    citation = Citation.model_validate(citation)
    raw = deps.raw.get(citation.snapshot_sha256)
    assert hashlib.sha256(raw).hexdigest() == citation.snapshot_sha256
    assert citation.fetched_at == T0 and citation.snapshot_id
    assert citation.usage_scope.value == "internal_only"
    restored = deps.tools.cite(citation.snapshot_sha256, citation.locator,
                               snapshot_id=citation.snapshot_id)["data"]
    assert restored["status"] == "ok" and restored["passages"]
    return citation, restored["passages"]


@pytest.mark.parametrize("source,fixture,count", [
    ("jp-ksj", "ksj/P05-22_41_GML.zip", 21),
    ("jp-census", "census2024/c1_001_1.xlsx", 1),
])
@pytest.mark.parametrize("blocked", [False, True])
def test_each_source_fetch_parse_query_and_citation(tmp_path, source, fixture, count, blocked):
    spec = load_registry(ROOT / "sources").get(source)
    data_dir = tmp_path / source
    deps = wiring.build(data_dir, {"registry": RegistryImpl([spec]), "vectors": None})
    try:
        crawler = deps.crawler_classes[spec.kind]()
        task, = crawler.plan(CrawlContext(spec, deps.manifest, deps.raw, T0))
        robots = tmp_path / "robots.synthetic.txt"
        robots.write_text("User-agent: *\nDisallow: /\n" if blocked else "User-agent: *\nDisallow:\n")
        transport = ReplayTransport({
            f"https://{spec.allowed_hosts[0]}/robots.txt": Recorded(robots),
            task.url: Recorded(ROOT / "tests/fixtures" / fixture,
                               headers={"content-type": "text/html; charset=ascii"}),
        })
        with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: T0,
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = run_crawl(spec, crawler, CrawlDeps(raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
                                                       now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None))
        assert all(urlsplit(url).hostname in spec.allowed_hosts for url in transport.requests)
        control = read_control(data_dir)
        assert control["enabled"] is False and not control["sources"].get(source, {}).get("enabled", False)
        if blocked:
            assert report.failures == {"blocked_by_robots": 1}
            assert task.url not in transport.requests
            return
        assert not report.failures and report.new == 1
        parsed = deps.parse_pending(deps.pipeline_deps, source)
        assert len(parsed.parsed) == 1 and not parsed.failed
        assert deps.reindex(deps.pipeline_deps, source) == count
        if source == "jp-ksj":
            hits = deps.retriever.search(["佐賀市 公民館"], k=10, source_ids=[source]).data
            hit = next(h for h in hits if "嘉瀬公民館" in h.snippet.content)
            citation, passages = restore(deps, hit.citation)
            assert citation.resource_url == task.url and citation.time_basis is TimeBasis.no_data_date
            assert parse_locator(citation.locator)["member"] == "P05-22_41.geojson"
            assert "CC BY 4.0" in citation.render()
            assert any("佐賀市嘉瀬町中原1690" in p["text"]["content"] for p in passages)
            catalog = deps.tools.search_catalog("国土数値情報", sources=source)["data"]["datasets"]
            item, = catalog
            restore(deps, item["citation"])
        else:
            result = deps.tools.query_sql(
                "SELECT obs_id, value FROM observations "
                "WHERE source_id = 'jp-census' AND area_code = '41201' AND indicator = '従業者数'"
            )["data"]
            assert len(result["rows"]) == len(result["provenance"]) == 1
            assert Decimal(result["rows"][0][1]) == 108887
            citation, passages = restore(deps, result["provenance"][0]["citation"])
            assert citation.locator == "sheet=c1_001_1;cell=E1745"
            assert citation.resource_url == task.url and citation.time_basis is TimeBasis.period
            assert citation.period.label == "令和６年"
            assert any(p["kind"] == "observation" and Decimal(p["value"]) == 108887 for p in passages)
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog"):
            getattr(deps, name).close()


@pytest.mark.parametrize("source", ["jp-ksj", "jp-census"])
def test_oversize_file_is_not_saved_or_promoted(tmp_path, source):
    spec = load_registry(ROOT / "sources").get(source)
    deps = wiring.build(tmp_path / source, {"registry": RegistryImpl([spec]), "vectors": None})
    try:
        crawler = deps.crawler_classes[spec.kind]()
        task, = crawler.plan(CrawlContext(spec, deps.manifest, deps.raw, T0))
        path = tmp_path / "oversize.synthetic.bin"
        path.write_bytes(b"a" * (25 * 1024 * 1024 + 1))
        transport = ReplayTransport({
            f"https://{spec.allowed_hosts[0]}/robots.txt": Recorded(status=404), task.url: Recorded(path),
        })
        with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: T0,
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = run_crawl(spec, crawler, CrawlDeps(raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
                                                       now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None))
        assert report.failures == {"too_large": 1} and report.new == 0
        snapshot, = deps.manifest.snapshots_for(source)
        assert snapshot.sha256 is None
        assert not deps.manifest.last_success(task.url)
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog"):
            getattr(deps, name).close()
