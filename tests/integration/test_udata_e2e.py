import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from datumoj.rikolto import wiring
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.registry import RegistryImpl, load_registry
from datumoj.rikolto.core.runner import CrawlDeps

ROOT = Path(__file__).parents[2]
FIX = ROOT / "tests/fixtures/udata_fr"
NOW = datetime(2026, 10, 26, tzinfo=UTC)


def test_catalog_offline(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    original = load_registry(ROOT / "sources").get("fr-data-gouv")
    spec = original.model_copy(update={"scope": {**original.scope, "page_size": 2}})
    deps = wiring.build(tmp_path / "db", {"registry": RegistryImpl([spec])})
    base = spec.base_url + "datasets/?organization=" + spec.scope["organization"] + "&page_size=2&page="
    last = json.loads((FIX / "datasets_org-insee_page2_size2.json").read_bytes())
    last["next_page"] = None
    terminal = tmp_path / "terminal.json"
    terminal.write_text(json.dumps(last), encoding="utf-8")
    transport = ReplayTransport({"https://www.data.gouv.fr/robots.txt": Recorded(status=404),
                                 base + "1": Recorded(FIX / "datasets_org-insee_page1_size2.json"),
                                 base + "2": Recorded(terminal)})
    try:
        with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: NOW,
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = deps.run_crawl(spec, deps.crawler_classes[spec.kind](), CrawlDeps(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
                now=lambda: NOW, clock=lambda: 0.0, sleep=lambda _: None))
        assert not report.failures and report.stop_reason is None
        parsed = deps.parse_pending(deps.pipeline_deps, spec.id)
        assert not parsed.failed and not parsed.no_parser
        deps.reindex(deps.pipeline_deps)
        results = deps.tools.search_catalog("population Iris", sources=spec.id)["data"]["datasets"]
        assert any(d["dataset_key"] == "fr-data-gouv:6ad16a7506eea90157a47a35" for d in results)
        assert len(transport.requests) == 3
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog"):
            getattr(deps, name).close()
