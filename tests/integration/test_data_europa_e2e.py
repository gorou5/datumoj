import httpx
import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.runner import CrawlDeps
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import T0
from tests.crawlers.test_data_europa import BASE, FIX, PAGE, SPEC


def test_offline_catalog_search(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network access is forbidden")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    spec = SPEC.model_copy(update={"scope": {"catalogue": "publ", "limit": 5}, "max_files": 20})
    deps = wiring.build(tmp_path / "data", {"registry": FakeRegistry([spec])})
    robots = tmp_path / "robots.txt"
    robots.write_text("User-agent: *\nDisallow: /*search?*\nDisallow: /*&sort=*\n")
    listing = tmp_path / "list.json"
    listing.write_text('["combined-nomenclature-2023"]')
    routes = {"https://data.europa.eu/robots.txt": Recorded(robots), PAGE + "0": Recorded(listing),
              f"{BASE}/datasets/combined-nomenclature-2023": Recorded(FIX / "repo_dataset_combined-nomenclature-2023.jsonld")}
    try:
        with PoliteHttpFetcher.from_spec(spec, transport=ReplayTransport(routes), now=lambda: T0,
                                         clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = deps.run_crawl(spec, deps.crawler_classes[spec.kind](), CrawlDeps(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher, now=lambda: T0,
                clock=lambda: 0.0, sleep=lambda _: None))
        assert not report.failures and report.stop_reason is None
        parsed = deps.parse_pending(deps.pipeline_deps, spec.id)
        assert not parsed.failed and not parsed.no_parser
        assert any("combined-nomenclature-2023" in hit.dataset.dataset_key for hit in deps.catalog.search("combined nomenclature"))
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()


def test_max_files_stops_and_reports_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    spec = SPEC.model_copy(update={"max_files": 1})
    deps = wiring.build(tmp_path / "data", {"registry": FakeRegistry([spec])})
    robots = tmp_path / "robots.txt"
    robots.write_text("User-agent: *\nDisallow: /*search?*\nDisallow: /*&sort=*\n")
    routes = {"https://data.europa.eu/robots.txt": Recorded(robots),
              PAGE + "0": Recorded(FIX / "repo_catalogue-publ_identifiers_limit5_offset0.json")}
    transport = ReplayTransport(routes)
    try:
        with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: T0,
                                         clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = deps.run_crawl(spec, deps.crawler_classes[spec.kind](), CrawlDeps(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher, now=lambda: T0,
                clock=lambda: 0.0, sleep=lambda _: None))
        assert report.stop_reason == "因上限停止：max_files=1"
        assert len(transport.requests) == 2  # robots and the listing; no detail request
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()
