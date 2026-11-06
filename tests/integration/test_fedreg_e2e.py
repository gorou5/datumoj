"""Federal Register: real runner, parser, stores and index over offline recordings."""

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.contracts import CrawlContext
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import FetchOutcome, SourceText, TimeBasis
from datumoj.rikolto.core.registry import RegistryImpl, load_registry
from datumoj.rikolto.core.runner import CrawlDeps, run_crawl
from datumoj.rikolto.core.schedule import read_control
from datumoj.rikolto.crawlers.fedreg import FedregCrawler
from datumoj.rikolto.testing.samples import T0

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/fedreg"


@pytest.mark.parametrize("block_xml", [False, True])
def test_fetch_parse_index_search_and_citation(tmp_path, monkeypatch, block_xml):
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    spec = load_registry(ROOT / "sources").get("us-fedreg")
    detail = json.loads((FIXTURES / "document_2026-20122.json").read_bytes())
    # Only one recorded detail/full text exists. Project discovery onto that
    # unmodified record, and explicitly mark the envelope as synthetic.
    recorded_search = json.loads((FIXTURES / "documents_rule_gte2026-10-01_p1.json").read_bytes())
    record, = [r for r in recorded_search["results"] if r["document_number"] == detail["document_number"]]
    search_path = tmp_path / "search.synthetic.json"
    search_path.write_text(json.dumps({"count": 1, "total_pages": 1, "results": [record]}))
    robots_path = tmp_path / "robots.synthetic.txt"
    robots_path.write_text("User-agent: *\nDisallow: /documents/full_text/xml/\n" if block_xml else
                           "User-agent: *\nDisallow: /documents/search\n")
    deps = wiring.build(tmp_path / "data", {"registry": RegistryImpl([spec]), "vectors": None})
    try:
        ctx = CrawlContext(spec, deps.manifest, deps.raw, T0)
        search_task = next(iter(FedregCrawler().plan(ctx)))
        transport = ReplayTransport({
            "https://www.federalregister.gov/robots.txt": Recorded(robots_path),
            search_task.url: Recorded(search_path, headers={"content-type": "text/html"}),
            spec.base_url + "documents/2026-20122.json": Recorded(FIXTURES / "document_2026-20122.json"),
            detail["full_text_xml_url"]: Recorded(FIXTURES / "full_text_2026-20122.xml",
                                                 headers={"content-type": "text/html; charset=cp932"}),
        })
        with PoliteHttpFetcher.from_spec(
            spec, transport=transport, now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None,
        ) as fetcher:
            report = run_crawl(spec, deps.crawler_classes[spec.kind](), CrawlDeps(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
                now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None,
            ))
        assert all(url.startswith("https://www.federalregister.gov/") for url in transport.requests)
        control = read_control(tmp_path / "data")
        assert control["enabled"] is False
        assert control["sources"].get(spec.id, {}).get("enabled", False) is False
        if block_xml:
            assert report.failures == {"blocked_by_robots": 1}
            assert detail["full_text_xml_url"] not in transport.requests
            snap = deps.manifest.snapshots_for(spec.id)[-1]
            assert snap.outcome is FetchOutcome.blocked_by_robots
            assert next(iter(FedregCrawler().plan(ctx))).meta["start"] == "2026-10-01"
            return
        assert not report.failures and report.stop_reason is None
        assert report.new == 3
        parsed = deps.parse_pending(deps.pipeline_deps, spec.id)
        assert not parsed.failed and len(parsed.parsed) == 3
        assert deps.reindex(deps.pipeline_deps, spec.id) == 22
        result = deps.retriever.search(["overhead electrical transmission wires"], k=10, source_ids=[spec.id])
        assert result.data
        hit = next(h for h in result.data if "II. Background and Authority" in h.locator)
        assert hit.doc_key == "us-fedreg:2026-20122"
        assert isinstance(hit.snippet, SourceText) and "NYPA" in hit.snippet.content
        citation = hit.citation
        assert citation.published_at == date(2026, 10, 1)
        assert citation.time_basis is TimeBasis.published
        assert "2026-20122" in citation.resource_url
        assert citation.resource_url == detail["full_text_xml_url"]
        assert "2026-10-01" in citation.render()
        assert citation.snapshot_id and citation.fetched_at == T0
        assert hashlib.sha256(deps.raw.get(citation.snapshot_sha256)).hexdigest() == citation.snapshot_sha256
        doc = deps.chunks.get_document(hit.doc_key)
        assert doc.metadata["html_url"] == detail["html_url"]
        assert doc.metadata["document_number"] == "2026-20122"
        restored = deps.tools.cite(citation.snapshot_sha256, citation.locator)
        assert restored["data"]["status"] == "ok"
        assert "NYPA" in restored["data"]["passages"][0]["text"]["content"]
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog"):
            getattr(deps, name).close()
