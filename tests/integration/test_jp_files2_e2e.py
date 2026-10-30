"""Independent offline acceptance for the NDB and ABR source plugins."""

import hashlib
import io
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

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
        pytest.fail('Government HTTP must use ReplayTransport')
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', forbidden)
    monkeypatch.setenv('DATUMOJ_RAW_BACKEND', 'local')
    monkeypatch.setenv('DATUMOJ_VECTORS', 'off')


def restore(deps, citation):
    citation = Citation.model_validate(citation)
    raw = deps.raw.get(citation.snapshot_sha256)
    assert hashlib.sha256(raw).hexdigest() == citation.snapshot_sha256
    assert citation.snapshot_id and citation.fetched_at == T0
    assert citation.usage_scope.value == 'internal_only'
    restored = deps.tools.cite(citation.snapshot_sha256, citation.locator,
                               snapshot_id=citation.snapshot_id)['data']
    assert restored['status'] == 'ok' and restored['passages']
    return citation, restored['passages']


def recorded_archive(tmp_path, spec):
    if spec.id == 'jp-abr':
        return ROOT / 'tests/fixtures/abr/mt_town_city134023.csv.zip'
    # The NDB fixture is derived; wrap its untouched bytes in the original cp932 member name.
    payload = (ROOT / 'tests/fixtures/ndb/ndb11_kenshin_shitsumon01_pref_sex_age.xlsx').read_bytes()
    member = spec.scope['member'].encode('cp932')
    placeholder = b'a' * len(member)
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr(placeholder.decode(), payload)
    path = tmp_path / 'ndb.synthetic.zip'
    path.write_bytes(output.getvalue().replace(placeholder, member))
    return path


@pytest.mark.parametrize('source', ['jp-mhlw', 'jp-abr'])
@pytest.mark.parametrize('blocked', [False, True])
def test_each_source_fetch_parse_query_and_citation(tmp_path, source, blocked):
    spec = load_registry(ROOT / 'sources').get(source)
    data_dir = tmp_path / source
    deps = wiring.build(data_dir, {'registry': RegistryImpl([spec]), 'vectors': None})
    try:
        crawler = deps.crawler_classes[spec.kind]()
        task, = crawler.plan(CrawlContext(spec, deps.manifest, deps.raw, T0))
        robots = tmp_path / 'robots.synthetic.txt'
        robots.write_text('User-agent: *\nDisallow: /\n' if blocked else 'User-agent: *\nDisallow:\n')
        transport = ReplayTransport({
            f'https://{spec.allowed_hosts[0]}/robots.txt': Recorded(robots),
            task.url: Recorded(recorded_archive(tmp_path, spec), headers={'content-type': 'text/html; charset=ascii'}),
        })
        with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: T0,
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = run_crawl(spec, crawler, CrawlDeps(raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
                                                       now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None))
        assert all(urlsplit(url).hostname in spec.allowed_hosts for url in transport.requests)
        control = read_control(data_dir)
        assert control['enabled'] is False and not control['sources'].get(source, {}).get('enabled', False)
        if blocked:
            assert report.failures == {'blocked_by_robots': 1}
            assert task.url not in transport.requests
            return
        assert not report.failures and report.new == 1
        parsed = deps.parse_pending(deps.pipeline_deps, source)
        assert len(parsed.parsed) == 1 and not parsed.failed
        indexed = deps.reindex(deps.pipeline_deps, source)
        if source == 'jp-mhlw':
            assert indexed == 0
            result = deps.tools.query_sql(
                "SELECT obs_id, value, unit FROM observations WHERE source_id = 'jp-mhlw' "
                "AND obs_id = 'ndb11-q1:北海道:男:40～44歳:はい'"
            )['data']
            assert len(result['rows']) == len(result['provenance']) == 1
            assert Decimal(result['rows'][0][1]) == 4688 and result['rows'][0][2]['content'] == '人数'
            citation, passages = restore(deps, result['provenance'][0]['citation'])
            assert citation.resource_url == task.url and citation.time_basis is TimeBasis.period
            assert citation.period.label == '2023年度'
            assert (citation.period.start.isoformat(), citation.period.end.isoformat()) == ('2023-04-01', '2024-03-31')
            assert parse_locator(citation.locator) == {'member': spec.scope['member'], 'sheet': '質問項目', 'cell': 'C6'}
            assert any(p['kind'] == 'observation' and Decimal(p['value']) == 4688 for p in passages)
            assert '厚生労働省' in citation.render() and 'PDL1.0' in citation.render()
        else:
            assert indexed == 4
            hits = deps.retriever.search(['青ヶ島村'], k=10, source_ids=[source]).data
            assert hits
            hit = next(h for h in hits if '休戸郷' in h.snippet.content)
            citation, passages = restore(deps, hit.citation)
            assert citation.resource_url == task.url and citation.time_basis is TimeBasis.no_data_date
            locator = parse_locator(citation.locator)
            assert locator == {'member': 'mt_town_city134023.csv', 'row': '2'}
            content = json.loads(passages[0]['text']['content'].split('\n', 1)[1])
            assert content['machiaza_id'] == '0003000' and content['ablt_date'] == ''
    finally:
        for name in ('manifest', 'chunks', 'keyword', 'catalog'):
            getattr(deps, name).close()


@pytest.mark.parametrize('source', ['jp-mhlw', 'jp-abr'])
def test_oversize_file_is_not_saved_or_promoted(tmp_path, source):
    spec = load_registry(ROOT / 'sources').get(source)
    deps = wiring.build(tmp_path / source, {'registry': RegistryImpl([spec]), 'vectors': None})
    try:
        crawler = deps.crawler_classes[spec.kind]()
        task, = crawler.plan(CrawlContext(spec, deps.manifest, deps.raw, T0))
        path = tmp_path / 'oversize.synthetic.bin'
        path.write_bytes(b'a' * (25 * 1024 * 1024 + 1))
        transport = ReplayTransport({
            f'https://{spec.allowed_hosts[0]}/robots.txt': Recorded(status=404), task.url: Recorded(path),
        })
        with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: T0,
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = run_crawl(spec, crawler, CrawlDeps(raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
                                                       now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None))
        assert report.failures == {'too_large': 1} and report.new == 0
        snapshot, = deps.manifest.snapshots_for(source)
        assert snapshot.sha256 is None and not deps.manifest.last_success(task.url)
    finally:
        for name in ('manifest', 'chunks', 'keyword', 'catalog'):
            getattr(deps, name).close()
