"""m3: fixture replay → changes/weekly/alerts → all banks → real stdio MCP.

Only DATUMOJ_EVIDENCE_DIR opts into persistent evidence. Modified responses and
monitor history are explicitly synthetic and always live in pytest temp paths.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
import pytest
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

from datumoj import cli, pipeline, wiring
from datumoj.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.core.models import ChangeKind, Citation, SourceText
from datumoj.core.registry import RegistryImpl, load_registry
from datumoj.core.runner import CrawlDeps
from datumoj.eval import check_gate, evaluate, load_questions, write_reports
from tests.eval.test_questions_tachibana import GOV_WEB_FIXTURES
from tests.integration.test_full_eval import AnnouncementParser, _seed

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures"
FIRST = datetime(2026, 10, 21, 12, tzinfo=UTC)
SECOND = datetime(2026, 10, 22, 12, tzinfo=UTC)
BASE = "https://example.invalid/policy/startup/"
MODIFIED = BASE + "detail-a.html"
REMOVED = BASE + "deeper/detail-b.html"
ADDED = BASE + "deeper/deep/detail-c.html"


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


@pytest.fixture(scope="module")
def m3(tmp_path_factory):
    temporary = tmp_path_factory.mktemp("tachibana-m3")
    base = os.environ.get("DATUMOJ_EVIDENCE_DIR")
    evidence = Path(base) / "tachibana-m3" if base else temporary / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    registry = load_registry(ROOT / "sources")
    original = registry.get("jp-gov-web")
    policy = {url: Recorded(FIXTURES / relative, headers={
        "content-type": "application/pdf" if kind == "pdf" else "text/html",
    }) for rows in GOV_WEB_FIXTURES.values() for relative, url, kind in rows}
    site = FIXTURES / "gov_web/html/synthetic/site/policy/startup"
    # The crawler's small-site fixtures are shorter than the production parser's
    # 100-character body minimum. Extend only temporary synthetic copies.
    site_routes = {}
    for url, relative in ((MODIFIED, "detail-a.html"), (REMOVED, "deeper/detail-b.html"),
                          (ADDED, "deeper/deep/detail-c.html")):
        path = temporary / (Path(relative).stem + ".synthetic.html")
        extra = ("<p>これは架空市の統合試験専用の合成資料です。実在する制度の案内ではありません。"
                 "相談の前に対象地域と申請期間、必要な書類をご確認ください。"
                 "公開資料に記載のない条件や採択結果は、この資料から推定できません。"
                 "本試験では変更前後の本文と出典を保存し、履歴を確認します。</p>")
        path.write_bytes((site / relative).read_bytes().replace(b"</main>", extra.encode() + b"</main>"))
        site_routes[url] = Recorded(path)
    policy.update({url: site_routes[url] for url in (MODIFIED, REMOVED)})
    seeds = [{"url": url, "label": "Offline m3 fixture"} for url in policy]
    spec = original.model_copy(update={
        "allowed_hosts": sorted({urlsplit(url).hostname for url in policy}),
        "scope": {**original.scope, "seeds": seeds, "max_depth": 0, "follow_pdf": False},
    })
    registry = RegistryImpl([
        *(spec if s.id == spec.id else s for s in registry.all()),
        *load_registry(FIXTURES / "eval_extra/sources").all(),
    ])
    parsers = wiring._connected_parsers()
    parsers[("eval_fixture", "text/plain")] = AnnouncementParser
    clock = [FIRST]

    class ReplayDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    def forbidden(*args, **kwargs):
        pytest.fail("m3 HTTP must use ReplayTransport")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATUMOJ_RAW_BACKEND", "local")
        patch.setenv("DATUMOJ_VECTORS", "off")
        patch.setenv("DATUMOJ_DATA_DIR", str(temporary / "data"))
        patch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
        patch.setattr(pipeline, "datetime", ReplayDateTime)
        deps = wiring.build(temporary / "data", {"registry": registry, "parsers": parsers})
        requests, reports = [], []

        def parse():
            result = deps.parse_pending(deps.pipeline_deps)
            assert not result.failed and not result.no_parser, result
            deps.reindex(deps.pipeline_deps)

        def crawl(source, routes):
            source_spec = registry.get(source)
            routes = {**routes, **{f"https://{host}/robots.txt": Recorded(status=404)
                                     for host in source_spec.allowed_hosts}}
            transport = ReplayTransport(routes)
            with PoliteHttpFetcher.from_spec(
                source_spec, transport=transport, now=lambda: clock[0],
                clock=lambda: 0.0, sleep=lambda _: None,
            ) as fetcher:
                result = deps.run_crawl(source_spec, deps.crawler_classes[source_spec.kind](), CrawlDeps(
                    raw=deps.raw, manifest=deps.manifest, fetcher=fetcher, now=lambda: clock[0],
                    clock=lambda: 0.0, sleep=lambda _: None,
                ))
            assert result.stop_reason is None, result
            requests.append({"source": source, "round": clock[0].isoformat(), "urls": transport.requests})
            reports.append({"source": source, **vars(result)})
            return result

        stats = {}
        for table in registry.get("jp-estat-files").scope["tables"]:
            path, = (FIXTURES / "estat_files").glob(f"{table['stat_inf_id']}_*")
            query = urlencode({"statInfId": table["stat_inf_id"], "fileKind": 4 if path.suffix == ".xlsx" else 1})
            stats[f"https://www.e-stat.go.jp/stat-search/file-download?{query}"] = Recorded(path)
        try:
            _seed(deps, evidence)
            parse()
            for source, routes in (("jp-gov-web", policy), ("jp-estat-files", stats)):
                assert not crawl(source, routes).failures
            parse()
            first_events = deps.change_log.list(limit=1000)
            assert first_events

            # Second round: new policy, materially revised policy, HTTP 410, and
            # one changed numeric cell in a temporary copy of the Osaka CSV.
            clock[0] = SECOND
            changed = temporary / "detail-a.modified.synthetic.html"
            changed.write_bytes(Path(site_routes[MODIFIED].path).read_bytes().replace(
                "創業時の経費の一部を補助します。".encode(),
                "本制度は終了しました。新しい相談支援のみを実施します。".encode(),
            ))
            assert changed.read_bytes() != Path(site_routes[MODIFIED].path).read_bytes()
            seeds.append({"url": ADDED, "label": "Synthetic second round addition"})
            policy.update({MODIFIED: Recorded(changed), REMOVED: Recorded(status=410),
                           ADDED: site_routes[ADDED]})
            stats_url = next(url for url in stats if "000031721271" in url)
            csv = temporary / "osaka.modified.synthetic.csv"
            original_csv = Path(stats[stats_url].path).read_bytes()
            csv.write_bytes(original_csv.replace(b"392940", b"392941", 1))
            assert csv.read_bytes() != original_csv
            stats[stats_url] = Recorded(csv)
            failed = crawl("jp-gov-web", policy)
            assert sum(failed.failures.values()) == 1
            assert not crawl("jp-estat-files", stats).failures
            parse()
            events = deps.change_log.list(since=SECOND.replace(hour=0), limit=1000)
            assert Counter(event.kind for event in events) == {
                ChangeKind.added: 1, ChangeKind.modified: 2,
                ChangeKind.removed: 1, ChangeKind.fetch_failed: 1,
            }
            by_url = {(e.kind, e.resource_url): e for e in events}
            assert (ChangeKind.added, ADDED) in by_url
            assert (ChangeKind.modified, MODIFIED) in by_url
            assert (ChangeKind.modified, stats_url) in by_url
            removed = by_url[ChangeKind.removed, REMOVED]
            assert removed.old_sha256 and removed.new_sha256 is None
            before = {e.event_id for e in deps.change_log.list(limit=1000)}
            parse()
            assert {e.event_id for e in deps.change_log.list(limit=1000)} == before
            _json(evidence / "requests.json", requests)
            _json(evidence / "crawls.json", reports)
            _json(evidence / "events.json", [e.model_dump(mode="json") for e in events])
            yield {"deps": deps, "temporary": temporary, "evidence": evidence, "events": events}
        finally:
            _close(deps)


def test_all_ivy_and_tachibana_questions_pass(m3, monkeypatch):
    deps = m3["deps"]
    assert deps.tools.change_log is deps.change_log is deps.pipeline_deps.change_log
    questions = load_questions(ROOT / "eval/questions")
    counts = Counter(Path(q._source_file).name for q in questions)
    assert sum(counts[name] for name in ("law.yaml", "stats.yaml", "catalog.yaml", "behavior.yaml")) >= 60
    assert counts["gov_web.yaml"] + counts["tachibana_gaps.yaml"] == 20
    assert counts["changes.yaml"] == 5

    def cite(citation):
        deps.raw.verify(citation.snapshot_sha256)
        data = deps.tools.cite(citation.snapshot_sha256, citation.locator)["data"]
        assert data["status"] == "ok"
        return data["passages"]

    report = evaluate(questions, retriever=deps.retriever, observation_store=deps.observations,
                      cite=cite, manifest=deps.manifest, catalog=deps.catalog,
                      list_changes=deps.tools.list_changes)
    write_reports(report, m3["evidence"])
    gate = check_gate(report)
    _json(m3["evidence"] / "gate.json", vars(gate))
    assert gate.passed, (gate.failures, report.failures)
    assert report.overall["change_hit_rate"] == 1
    # Check each bank independently, so aggregate recall cannot mask regressions.
    for bank in counts:
        rows = [row for row in report.questions if Path(row["file"]).name == bank]
        for metric, threshold in gate.thresholds.items():
            values = [row["metrics"][metric] for row in rows if row["metrics"][metric] is not None]
            if values:
                assert sum(values) / len(values) >= threshold, (bank, metric, rows)

    monkeypatch.setattr(cli.wiring, "build", lambda path: deps)
    result = CliRunner().invoke(cli.app, ["eval", "--gate", "--report", str(m3["temporary"] / "cli-eval")])
    assert result.exit_code == 0, result.output
    _json(m3["evidence"] / "cli-eval.json", {"exit_code": result.exit_code, "output": result.output})


def test_weekly_report_and_monitor_alerts_match_events(m3, monkeypatch):
    deps = m3["deps"]
    # Explicitly manufactured run history: three consecutive failures and a JMA
    # gap longer than its retention window. No host scheduler is installed.
    history = []
    for offset in (3, 2, 1):
        at = SECOND - timedelta(hours=offset)
        history.append({"source_id": "jp-gov-web", "started_at": at.isoformat(),
                        "ended_at": at.isoformat(), "status": "completed", "exit_status": 1})
    at = SECOND - timedelta(days=10)
    history.append({"source_id": "jp-jma", "started_at": at.isoformat(),
                    "ended_at": at.isoformat(), "status": "completed", "exit_status": 0})
    (deps.data_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in history))
    monkeypatch.setattr(cli.wiring, "build", lambda path: deps)
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(deps.data_dir))
    scheduler = cli.scheduling.Scheduler
    monkeypatch.setattr(cli.scheduling, "Scheduler", lambda *a, **kw: scheduler(*a, **kw, now=lambda: SECOND))
    cli.scheduling.set_enabled(deps.data_dir, True)
    cli.scheduling.set_enabled(deps.data_dir, True, ["jp-jma"])
    runner = CliRunner()
    status = runner.invoke(cli.app, ["schedule", "status"])
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    alerts = {(row["source_id"], alert["rule"]) for row in payload["sources"] for alert in row["alerts"]}
    assert ("jp-gov-web", "consecutive_failures") in alerts
    assert ("jp-jma", "retention_gap") in alerts
    _json(m3["evidence"] / "schedule-status.json", payload)
    out = m3["evidence"] / "weekly.md"
    result = runner.invoke(cli.app, ["report", "weekly", "--since", "2026-10-22",
                                   "--until", "2026-10-22", "--out", str(out)])
    assert result.exit_code == 0, result.output
    report = out.read_text()
    labels = {ChangeKind.added: "新增", ChangeKind.modified: "修改", ChangeKind.removed: "删除",
              ChangeKind.fetch_failed: "抓取失败", ChangeKind.parse_failed: "解析失败"}
    for spec in deps.registry.all():
        name = spec.name.replace("-", "\\-")
        section = report.split(f"## 来源：{name}\n", 1)[1].split("\n## ", 1)[0]
        events = [e for e in m3["events"] if e.source_id == spec.id]
        counts = Counter(e.kind for e in events)
        assert f"| {name} | " + " | ".join(str(counts[kind]) for kind in labels) + " |" in report
        for kind, label in labels.items():
            part = section.split(f"### {label}（{counts[kind]}）\n", 1)[1].split("\n### ", 1)[0]
            assert sum(line.startswith("- ") for line in part.splitlines()) == max(1, counts[kind])
            for event in (e for e in events if e.kind == kind):
                sha = event.old_sha256 if kind == ChangeKind.removed else event.new_sha256 or event.old_sha256
                if kind == ChangeKind.fetch_failed:
                    assert "事件类型：fetch_failed" in part and "没有可还原的 locator" in part
                    assert "detail\\-b\\.html" in part
                else:
                    assert sha[:12] in part
        if not events:
            assert "本周没有变化。" in section
    assert "consecutive\\_failures" in report and "retention\\_gap" in report
    assert "### 本周有缺口的来源" in report


def test_new_tools_over_real_stdio(m3):
    deps = m3["deps"]
    # The child uses the production CLI and default wiring factory. Block HTTP
    # inside that process too; all evidence must come from the persisted corpus.
    bootstrap = (
        "import httpx; "
        "exec('def forbidden(*a, **kw):\\n raise RuntimeError(\"MCP must stay offline\")'); "
        "httpx.HTTPTransport.handle_request=forbidden; "
        "httpx.AsyncHTTPTransport.handle_async_request=forbidden; "
        "from datumoj.cli import app; app()"
    )
    params = StdioServerParameters(command=sys.executable,
        args=["-c", bootstrap, "--data-dir", str(deps.data_dir), "mcp"], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "DATUMOJ_DATA_DIR": str(deps.data_dir),
             "DATUMOJ_VECTORS": "off", "DATUMOJ_RAW_BACKEND": "local",
             "DATUMOJ_TOOLS_FACTORY": "datumoj.wiring:build_tools"})

    def data(result):
        assert not result.is_error, result.content
        return result.structured_content.get("result", result.structured_content)

    async def run():
        async with Client(params, read_timeout_seconds=30) as client:
            changes = data(await client.call_tool("list_changes", {
                "since": "2026-10-22", "until": "2026-10-22", "sources": "jp-gov-web",
                "kinds": ["added", "modified", "removed"],
            }))
            assert changes["gap_state"] == "ok"
            assert Counter(row["kind"] for row in changes["data"]) == {"added": 1, "modified": 1, "removed": 1}
            for row in changes["data"]:
                assert SourceText.model_validate(row["summary"]).content
                citation = Citation.model_validate(row["citation"])
                deps.raw.verify(citation.snapshot_sha256)
                restored = data(await client.call_tool("cite", {
                    "snapshot_sha256": citation.snapshot_sha256, "locator": citation.locator,
                }))
                assert restored["data"]["status"] == "ok" and restored["data"]["passages"]
            searches = {}
            for kind, query in (("html_page", "スタートアップ"), ("pdf", "スタートアップ")):
                found = data(await client.call_tool("search_docs", {
                    "queries": query, "sources": "jp-gov-web", "kinds": [kind], "k": 10,
                }))
                assert found["gap_state"] == "ok" and found["data"]["hits"]
                for hit in found["data"]["hits"]:
                    citation = Citation.model_validate(hit["citation"])
                    doc = deps.chunks.get_document(hit["doc_key"], citation.snapshot_sha256)
                    assert doc.kind.value == kind
                    restored = data(await client.call_tool("cite", {
                        "snapshot_sha256": citation.snapshot_sha256, "locator": citation.locator,
                    }))
                    assert restored["data"]["status"] == "ok"
                searches[kind] = found
            _json(m3["evidence"] / "mcp-stdio.json", {"list_changes": changes, "search_docs": searches})

    asyncio.run(run())
