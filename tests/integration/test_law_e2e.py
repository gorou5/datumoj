"""M1: real CLI/storage/crawler/parser/retriever, with offline HTTP replay.

The recorded list pages contain different laws from the full-text corpus. Only
discovery is synthetic: its entries copy metadata from every recorded full text.
No expected answers or question queries participate in corpus construction.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, wiring
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import Citation, FetchOutcome, SourceText, TimeBasis, parse_context_fingerprint
from datumoj.rikolto.core.runner import CrawlDeps
from datumoj.rikolto.crawlers.egov_law import EgovLawCrawler
from datumoj.rikolto.eval import evaluate, load_questions, write_reports
from datumoj.rikolto.index.hybrid import HybridRetriever
from datumoj.rikolto.parsers.xml_law import LawXMLParser

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/egov_law"
# 评测报告默认写到临时目录：写回仓库里受跟踪的 results/ 会弄脏工作区，调度器因此拒绝合并其他任务。
# 需要更新 results/DATUMOJ-IVY-901/ 里的证据时，设置 DATUMOJ_EVIDENCE_DIR 再运行。
RESULTS = Path(os.environ["DATUMOJ_EVIDENCE_DIR"]) if os.environ.get("DATUMOJ_EVIDENCE_DIR") else Path(tempfile.mkdtemp(prefix="datumoj-901-"))
SOURCE = "jp-egov-law"
API = "https://laws.e-gov.go.jp/api/2"
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
ASOF = NOW.date().isoformat()


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _routes():
    RESULTS.mkdir(parents=True, exist_ok=True)
    entries, inventory = [], []
    # Synthetic 404 means robots.txt imposes no restrictions; never calls a host.
    routes = {"https://laws.e-gov.go.jp/robots.txt": Recorded(status=404)}
    for path in sorted(FIXTURES.glob(f"law_data.*.asof{ASOF}.xml-in-json.json")):
        payload = json.loads(path.read_bytes())
        law_id = payload["law_info"]["law_id"]
        entries.append({
            "law_info": payload["law_info"],
            "revision_info": payload["revision_info"],
            "current_revision_info": payload["revision_info"],
        })
        revision = FIXTURES / f"law_revisions.{law_id}.json"
        for fixture in (path, revision):
            inventory.append({
                "path": str(fixture.relative_to(ROOT)),
                "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
            })
        routes[f"{API}/law_revisions/{law_id}"] = Recorded(revision, headers={"content-type": "application/json"})
        url = f"{API}/law_data/{law_id}?asof={ASOF}&response_format=json&law_full_text_format=xml"
        routes[url] = Recorded(path, headers={"content-type": "application/json"})
    assert len(entries) == 22
    discovery = RESULTS / "laws_list.synthetic.json"
    _write_json(discovery, {"total_count": len(entries), "count": len(entries), "laws": entries})
    _write_json(RESULTS / "corpus.json", inventory)
    routes[f"{API}/laws?limit=100&offset=0"] = Recorded(discovery, headers={"content-type": "application/json"})
    return routes, {entry["law_info"]["law_id"] for entry in entries}


def _close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog"):
        getattr(deps, name).close()


@pytest.fixture(scope="module")
def law_chain(tmp_path_factory):
    routes, law_ids = _routes()
    data_dir = tmp_path_factory.mktemp("law-e2e")
    transports, builds, commands = [], [], []
    real_build = wiring.build

    class ReplayFetcherFactory:
        @staticmethod
        def from_spec(spec):
            transport = ReplayTransport(routes)
            transports.append(transport)
            return PoliteHttpFetcher.from_spec(
                spec, transport=transport, now=lambda: NOW,
                clock=lambda: 0.0, sleep=lambda seconds: None,
            )

    def build(path):
        deps = real_build(path, {
            "fetcher": ReplayFetcherFactory,
            "crawl_deps_type": partial(
                CrawlDeps, now=lambda: NOW, clock=lambda: 0.0, sleep=lambda seconds: None,
            ),
            # Offline M1 measures the supported keyword-only retrieval mode.
            # No HashEmbedder, fake vectors, fake storage or fake retriever.
            "vectors": None,
        })
        builds.append(deps)
        return deps

    def forbid_network(*args, **kwargs):
        pytest.fail("M1 must use ReplayTransport for every HTTP request")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATUMOJ_DATA_DIR", str(data_dir))
        patch.setattr(cli.wiring, "build", build)
        patch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
        runner = CliRunner()

        def invoke(args):
            before = len(builds)
            result = runner.invoke(cli.app, args)
            commands.append({"args": args, "exit_code": result.exit_code, "output": result.output})
            _write_json(RESULTS / "commands.json", commands)
            for deps in builds[before:]:
                _close(deps)
            return result

        first = invoke(["crawl", SOURCE])
        parsed = invoke(["parse", SOURCE])
        indexed = invoke(["index", "--rebuild"])
        # JSON and human-readable search go through the public CLI command.
        query = "集会 結社 言論 出版 検閲の禁止"
        searched = invoke(["search", query, "--source", SOURCE, "-k", "10", "--json"])
        human = invoke(["search", query, "--source", SOURCE, "-k", "10"])
        second = invoke(["crawl", SOURCE])
        _write_json(RESULTS / "requests.json", [t.requests for t in transports])
        deps = build(data_dir)
        try:
            yield {
                "deps": deps, "law_ids": law_ids, "first": first, "parse": parsed,
                "index": indexed, "search": searched, "human": human, "second": second,
                "transports": transports,
            }
        finally:
            _close(deps)


def test_cli_replay_to_search_with_restorable_citation(law_chain):
    chain = law_chain
    for command in ("first", "parse", "index", "search", "human"):
        result = chain[command]
        assert result.exit_code == 0, result.output
    deps = chain["deps"]
    assert isinstance(deps.retriever, HybridRetriever)
    assert deps.crawler_classes["egov_law"] is EgovLawCrawler
    assert deps.pipeline_deps.parsers is None  # Real parser discovery.
    chunks = list(deps.chunks.iter_chunks([SOURCE]))
    assert {c.doc_key for c in chunks} == {f"{SOURCE}:{law_id}" for law_id in chain["law_ids"]}
    snapshots = deps.manifest.snapshots_for(SOURCE)
    for snap in snapshots:
        assert snap.outcome is FetchOutcome.ok
        deps.raw.verify(snap.sha256)
        record = deps.manifest.get_parse(
            snap.sha256, LawXMLParser.name, LawXMLParser.version,
            parse_context_fingerprint(snap, deps.manifest.snapshot_meta(snap.snapshot_id)),
        )
        assert record is not None and record.status.value == "ok"

    payload = json.loads(chain["search"].output)
    hit = next(
        h for h in payload["data"]["hits"]
        if h["doc_key"] == f"{SOURCE}:321CONSTITUTION" and h["locator"] == "art=21"
    )
    citation = Citation.model_validate(hit["citation"])
    assert citation.source_id == SOURCE
    assert citation.resource_url == (
        f"{API}/law_data/321CONSTITUTION?asof={ASOF}&response_format=json&law_full_text_format=xml"
    )
    assert citation.fetched_at == NOW
    assert citation.effective_at == date(2026, 10, 20)
    assert citation.time_basis is TimeBasis.effective
    assert citation.usage_scope == deps.registry.get(SOURCE).license.usage_scope
    assert hashlib.sha256(deps.raw.get(citation.snapshot_sha256)).hexdigest() == citation.snapshot_sha256
    assert SourceText.model_validate(hit["snippet"]).content
    restored = deps.tools.cite(citation.snapshot_sha256, citation.locator)
    assert restored["data"]["status"] == "ok"
    assert "表現の自由" in restored["data"]["passages"][0]["text"]["content"]
    assert citation.render() in chain["human"].output


def test_second_crawl_skips_all_law_downloads(law_chain):
    result = law_chain["second"]
    assert result.exit_code == 0, result.output
    assert "新增 0，未变化 1，失败 0" in result.output
    first, second = law_chain["transports"]
    assert len(first.requests) == 46  # robots + list + 22 revisions + 22 full texts
    assert second.requests == ["https://laws.e-gov.go.jp/robots.txt", f"{API}/laws?limit=100&offset=0"]
    snaps = law_chain["deps"].manifest.snapshots_for(SOURCE)
    assert len(snaps) == 46  # One new list observation; no duplicate law observations.
    assert sum(law_chain["deps"].manifest.snapshot_meta(s.snapshot_id).get("role") == "law_data" for s in snaps) == 22


def test_law_questions_recall_at_10(law_chain):
    deps = law_chain["deps"]
    questions = [q for q in load_questions(ROOT / "eval/questions") if Path(q._source_file).name == "law.yaml"]
    assert len(questions) == 20

    def cite(citation):
        result = deps.tools.cite(citation.snapshot_sha256, citation.locator)
        assert result["data"]["status"] == "ok"
        return [SourceText.model_validate(p["text"]) for p in result["data"]["passages"]]

    report = evaluate(questions, retriever=deps.retriever, observation_store=deps.observations, cite=cite, k=10)
    write_reports(report, RESULTS)
    _write_json(RESULTS / "retrieval.json", [
        {"id": q.id, "queries": q.queries, "hits": [
            {"doc_key": h.doc_key, "locator": h.locator, "score": h.score}
            for h in deps.retriever.search(q.queries, k=10, source_ids=q.source_ids).data
        ]} for q in questions
    ])
    assert report.overall["recall_at_10"] >= 0.85, report.failures
    assert report.overall["citation_recoverability"] == 1.0, report.failures
