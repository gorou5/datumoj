from __future__ import annotations

import json

from typer.testing import CliRunner

from datumoj import cli, wiring
from datumoj.core.runner import CrawlDeps, run_crawl
from datumoj.pipeline import PipelineDeps, parse_pending, reindex
from datumoj.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeCrawler,
    FakeHttpFetcher,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
    FakeRetriever,
    FakeResponse,
)
from datumoj.testing.samples import T0, make_spec


URL = "https://example.go.jp/a.txt"
BODY = "お知らせ\n\n人口統計の公開について。".encode()


def _dependencies(tmp_path):
    spec = make_spec("jp-test", kind="fake", scope={"urls": [URL]})
    registry = FakeRegistry([spec])
    raw = FakeRawStore(tmp_path)
    manifest = FakeManifest()
    chunks = FakeChunkStore()
    keyword = FakeKeywordIndex()
    catalog = FakeCatalogStore()
    observations = FakeObservationStore()
    parsers = {("fake", "text/plain"): FakeParser}
    pipeline_deps = PipelineDeps(
        manifest=manifest, raw=raw, registry=registry, chunks=chunks, keyword=keyword,
        observations=observations, catalog=catalog, vectors=None, parsers=parsers,
    )
    return wiring.build(tmp_path, {
        "registry": registry,
        "raw": raw,
        "manifest": manifest,
        "chunks": chunks,
        "keyword": keyword,
        "vectors": None,
        "catalog": catalog,
        "observations": observations,
        "retriever": FakeRetriever(chunks, manifest, registry),
        "pipeline_deps": pipeline_deps,
        "parse_pending": parse_pending,
        "reindex": reindex,
        "crawlers": {"fake": FakeCrawler},
        "fetcher": FakeHttpFetcher(
            {URL: FakeResponse(body=BODY, headers={"content-type": "text/plain; charset=utf-8"})}, now=lambda: T0
        ),
        "run_crawl": run_crawl,
        "crawl_deps_type": CrawlDeps,
    })


def _invoke(runner, command: list[str]):
    result = runner.invoke(cli.app, command)
    assert result.exception is None, result.output
    return result


def test_all_commands_with_injected_offline_dependencies(tmp_path, monkeypatch):
    deps = _dependencies(tmp_path)
    monkeypatch.setattr(cli.wiring, "build", lambda data_dir: deps)
    runner = CliRunner()

    dry = _invoke(runner, ["crawl", "jp-test", "--dry-run", "--limit", "1"])
    assert dry.exit_code == 0 and URL in dry.output
    assert not deps.fetcher_factory.requests

    crawled = _invoke(runner, ["crawl", "jp-test"])
    assert crawled.exit_code == 0 and "新增 1" in crawled.output

    parsed = _invoke(runner, ["parse", "jp-test"])
    assert parsed.exit_code == 0 and "成功 1" in parsed.output

    indexed = _invoke(runner, ["index", "--rebuild"])
    assert indexed.exit_code == 0 and "索引重建完成" in indexed.output

    found = _invoke(runner, ["search", "人口統計", "-k", "2"])
    assert found.exit_code == 0
    assert "お知らせ" in found.output and "section=2" in found.output
    assert "人口統計の公開について。" in found.output and "出处：jp-test" in found.output

    no_hits = _invoke(runner, ["search", "見つからない語"])
    assert no_hits.exit_code == 0
    assert "零命中" in no_hits.output and "jp-test：测试来源 jp-test" in no_hits.output

    searched_json = _invoke(runner, ["search", "人口統計", "--json"])
    payload = json.loads(searched_json.output)
    assert payload["data"]["hits"][0]["locator"] == "section=2"

    queried = _invoke(runner, ["sql", "SELECT obs_id FROM observations"])
    assert queried.exit_code == 0 and "查询没有返回任何行" in queried.output
    queried_json = _invoke(runner, ["sql", "SELECT obs_id FROM observations", "--json"])
    assert json.loads(queried_json.output)["data"]["columns"] == ["obs_id"]

    status = _invoke(runner, ["status"])
    assert status.exit_code == 0 and "jp-test：ok；快照 1" in status.output


def test_argument_errors_use_exit_code_2(tmp_path, monkeypatch):
    deps = _dependencies(tmp_path)
    monkeypatch.setattr(cli.wiring, "build", lambda data_dir: deps)
    result = CliRunner().invoke(cli.app, ["search", "人口", "-k", "0"])
    assert result.exit_code == 2

    result = CliRunner().invoke(cli.app, ["search", ""])
    assert result.exit_code == 2 and "参数错误" in result.output


def test_runtime_errors_use_exit_code_1(tmp_path, monkeypatch):
    deps = _dependencies(tmp_path)

    def fail_query(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    deps.tools.query_sql = fail_query
    monkeypatch.setattr(cli.wiring, "build", lambda data_dir: deps)
    result = CliRunner().invoke(cli.app, ["sql", "SELECT 1"])
    assert result.exit_code == 1
    assert "运行失败：RuntimeError: storage unavailable" in result.output


def test_missing_feature_is_reported_without_traceback(tmp_path, monkeypatch):
    unavailable = wiring.Wiring(data_dir=tmp_path, unavailable={"sql": "该功能尚未接入"})
    monkeypatch.setattr(cli.wiring, "build", lambda data_dir: unavailable)
    result = CliRunner().invoke(cli.app, ["sql", "SELECT 1"])
    assert result.exit_code == 1
    assert "该功能尚未接入" in result.output
    assert "Traceback" not in result.output
