import json

import httpx
import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.runner import CrawlDeps
from datumoj.rikolto.crawlers.estat_api import EstatAPICrawler
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import T0
from tests.crawlers.test_estat_api import SPEC, context
from tests.parsers.test_estat_api import single


def test_offline_pipeline_does_not_leak_key(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv('DATUMOJ_RAW_BACKEND', 'local')
    key = 'secret-example-e2e-key'
    monkeypatch.setenv('ESTAT_APP_ID', key)
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', lambda *a, **k: pytest.fail('network'))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', lambda *a, **k: pytest.fail('network'))
    scope = {**SPEC.scope, 'tables': SPEC.scope['tables'][:1], 'stats_codes': []}
    spec = SPEC.model_copy(update={'scope': scope})
    task, = EstatAPICrawler().plan(context(spec))
    data = tmp_path / 'data.json'
    data.write_text(json.dumps(single(), ensure_ascii=False))
    robots = tmp_path / 'robots.txt'
    robots.write_text('User-agent: *\nAllow: /\n')
    routes = {'https://api.e-stat.go.jp/robots.txt': Recorded(robots), task.url: Recorded(data)}
    deps = wiring.build(tmp_path / 'store', {'registry': FakeRegistry([spec]), 'vectors': None})
    try:
        with PoliteHttpFetcher.from_spec(spec, transport=ReplayTransport(routes), now=lambda: T0,
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = deps.run_crawl(spec, EstatAPICrawler(), CrawlDeps(raw=deps.raw, manifest=deps.manifest,
                fetcher=fetcher, now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None))
        assert not report.failures and report.stop_reason is None
        parsed = deps.parse_pending(deps.pipeline_deps, spec.id)
        assert not parsed.failed and not parsed.no_parser
        sql = "SELECT source_id, table_key, obs_id, snapshot_sha256, release_id, value FROM observations WHERE time_label='2021年12月'"
        answer = deps.tools.query_sql(sql)['data']
        assert str(answer['rows'][0][-1]) == '12547'
        assert 'appId=REDACTED' in answer['provenance'][0]['citation']['resource_url']
    finally:
        for name in ('manifest', 'chunks', 'keyword', 'catalog', 'change_log'):
            getattr(deps, name).close()
    assert key not in caplog.text
    for path in tmp_path.rglob('*'):
        if path.is_file():
            assert key.encode() not in path.read_bytes(), path.name
