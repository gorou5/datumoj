"""Offline regressions for public cache boundaries and crawler/embedding fixes."""

import json
import traceback
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import yaml

from datumoj.rikolto import wiring
from datumoj.rikolto.core.contracts import CrawlContext, EmbeddingError, FetchResult, FetchTask, SourceSpec
from datumoj.rikolto.core.models import FetchOutcome, LicenseInfo
from datumoj.rikolto.core.state import StateError, StateManager
from datumoj.rikolto.crawlers.estat_api import EstatAPICrawler
from datumoj.rikolto.crawlers.fedreg import FedregCrawler
from datumoj.rikolto.index.embed import OllamaEmbedder
from datumoj.rikolto.testing.fakes import FakeManifest, FakeParser, FakeRawStore, FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('timeout', [httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.TimeoutException])
@pytest.mark.parametrize('retries', [0, 2])
def test_non_read_timeouts_retry_without_splitting(monkeypatch, timeout, retries):
    batches, sleeps = [], []
    texts = [f'private input {i}' for i in range(32)]

    def handle(request):
        batches.append(json.loads(request.content)['input'])
        raise timeout(' '.join(texts), request=request)

    monkeypatch.setattr('datumoj.rikolto.index.embed.time.sleep', sleeps.append)
    with httpx.Client(base_url='http://ollama.invalid', transport=httpx.MockTransport(handle)) as client:
        embedder = OllamaEmbedder(dim=2, client=client, retries=retries, backoff=0.25)
        with pytest.raises(EmbeddingError, match=timeout.__name__) as error:
            embedder.embed(texts)
    assert batches == [texts] * (retries + 1)
    assert sleeps == [0.25 * 2 ** attempt for attempt in range(retries)]
    assert texts[0] not in ''.join(traceback.format_exception(error.value))


def test_read_timeouts_still_split_and_preserve_order():
    batches = []
    texts = ['1', '2', '3', '4']

    def handle(request):
        batch = json.loads(request.content)['input']
        batches.append(batch)
        if len(batch) > 1:
            raise httpx.ReadTimeout('synthetic read timeout', request=request)
        return httpx.Response(200, json={'embeddings': [[float(batch[0]), 1.0]]})

    with httpx.Client(base_url='http://ollama.invalid', transport=httpx.MockTransport(handle)) as client:
        assert OllamaEmbedder(dim=2, client=client, retries=0).embed(texts) == [
            [1.0, 1.0], [2.0, 1.0], [3.0, 1.0], [4.0, 1.0],
        ]
    assert batches == [texts, texts[:2], ['1'], ['2'], texts[2:], ['3'], ['4']]


@pytest.mark.parametrize('status', [201, 302, 400, 401, 403, 422])
def test_rejected_embedding_response_does_not_echo_private_input(status):
    private = 'synthetic private text that must stay out of logs'
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, content=request.content)

    with (
        httpx.Client(base_url='http://ollama.invalid', transport=httpx.MockTransport(handle)) as client,
        pytest.raises(EmbeddingError) as error,
    ):
        OllamaEmbedder(dim=2, client=client, retries=3).embed([private])
    assert str(error.value) == f'Ollama 返回 {status}'
    assert private not in ''.join(traceback.format_exception(error.value))
    assert len(requests) == 1


@pytest.fixture
def estat_catalog(monkeypatch):
    monkeypatch.setenv('ESTAT_APP_ID', 'synthetic-key')
    spec = SourceSpec.model_validate(yaml.safe_load((ROOT / 'sources/jp-estat-api.yaml').read_text()))
    spec = spec.model_copy(update={'scope': {'tables': [], 'stats_codes': ['00200524']}})
    ctx = CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
    crawler = EstatAPICrawler()
    task, = crawler.plan(ctx)
    body = json.dumps({'GET_STATS_LIST': {
        'RESULT': {'STATUS': 0}, 'DATALIST_INF': {'RESULT_INF': {'NEXT_KEY': 6}},
    }}).encode()
    result = FetchResult(url=task.redacted_url, outcome=FetchOutcome.not_modified,
                         fetched_at=T0, http_status=304)
    return crawler, ctx, task, body, result


def test_estat_catalog_304_uses_last_success_even_after_prior_304(estat_catalog):
    crawler, ctx, task, body, result = estat_catalog
    ctx.raw.put(body)
    previous = make_snapshot(data=body, url=task.redacted_url, source_id=ctx.spec.id)
    ctx.manifest.record_snapshot(previous)
    ctx.manifest.record_snapshot(previous.model_copy(update={
        'snapshot_id': 'synthetic-304', 'outcome': FetchOutcome.not_modified,
        'http_status': 304, 'sha256': None, 'size': None,
    }))
    assert ctx.manifest.latest_snapshot(task.redacted_url).outcome is FetchOutcome.not_modified
    next_task, = crawler.after_fetch(task, result, ctx)
    params = parse_qs(urlsplit(next_task.url).query)
    assert params['startPosition'] == ['6']
    assert params['statsCode'] == ['00200524']
    assert params['appId'] == ['synthetic-key']
    assert next_task.meta == task.meta


@pytest.mark.parametrize('missing', ['snapshot', 'raw'])
def test_estat_catalog_304_without_previous_body_returns_empty(estat_catalog, missing):
    crawler, ctx, task, body, result = estat_catalog
    if missing == 'raw':
        ctx.manifest.record_snapshot(make_snapshot(data=body, url=task.redacted_url, source_id=ctx.spec.id))
    assert crawler.after_fetch(task, result, ctx) == ()


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_fedreg_detail_only_emits_https_xml(scheme):
    spec = SourceSpec.model_validate(yaml.safe_load((ROOT / 'sources/us-fedreg.yaml').read_text()))
    ctx = CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
    crawler = FedregCrawler()
    list(crawler.plan(ctx))
    task = FetchTask(url=spec.base_url + 'documents/2026-12345.json',
                     meta={'role': 'detail', 'document_number': '2026-12345', 'run': T0.isoformat()})
    xml = f'{scheme}://www.federalregister.gov/documents/full_text/xml/2026-12345.xml'
    body = json.dumps({'document_number': '2026-12345', 'type': 'RULE', 'full_text_xml_url': xml}).encode()
    result = FetchResult(url=task.url, outcome=FetchOutcome.ok, content=body, fetched_at=T0, http_status=200)
    children = list(crawler.after_fetch(task, result, ctx))
    assert [child.url for child in children] == ([xml] if scheme == 'https' else [])


@pytest.mark.parametrize('source', ['jp-estat-api', 'intl-wdi'])
def test_sensitive_source_license_explicitly_keeps_default_restrictions(source):
    license_data = yaml.safe_load((ROOT / f'sources/{source}.yaml').read_text())['license']
    assert 'outflow' in license_data and license_data['outflow'] == 'no_outflow'
    assert 'cloud_model_ok' in license_data and license_data['cloud_model_ok'] is False
    defaults = LicenseInfo(name='synthetic license')
    assert license_data['outflow'] == defaults.outflow.value
    assert license_data['cloud_model_ok'] is defaults.cloud_model_ok


@pytest.mark.parametrize('corruption', [None, 'size', 'sha256'])
def test_missing_state_cache_downloads_remote_blocks_and_verifies(tmp_path, monkeypatch, corruption):
    monkeypatch.setenv('DATUMOJ_RAW_BACKEND', 'local')
    root = tmp_path / 'source'
    deps = wiring.build(root, {'registry': FakeRegistry([make_spec()]), 'vectors': None,
                               'parsers': {('fake', 'text/plain'): FakeParser}})
    store = FakeStateObjectStore()
    try:
        body = b'Synthetic title\n\nSynthetic private content'
        snapshot = make_snapshot(data=body)
        deps.raw.put(body)
        deps.manifest.record_snapshot(snapshot)
        assert deps.parse_pending(deps.pipeline_deps).parsed == [snapshot.snapshot_id]
        candidate = StateManager(store, root).push()
        manager = StateManager(store, root)
        manager.cache_dir = tmp_path / 'runner-temp' / 'rikolto-blocks'
        assert not manager.cache_dir.exists()
        gets = []
        original_get = store.get
        block = next(copy for copy in candidate.stores if copy.format == 'sqlite-block-v1')

        def get(key):
            gets.append(key)
            found = original_get(key)
            if key == block.key and corruption is not None:
                data, etag = found
                damaged = data + b'x' if corruption == 'size' else bytes([data[0] ^ 1]) + data[1:]
                return damaged, etag
            return found

        monkeypatch.setattr(store, 'get', get)
        target = tmp_path / 'restored'
        if corruption is not None:
            with pytest.raises(StateError, match='SHA256.*大小校验失败'):
                manager.restore(target, raw='remote')
            assert not target.exists()
            assert block.key in gets
        else:
            assert manager.restore(target, raw='remote')['verified']
            copies = [copy for copy in candidate.stores if copy.format != 'sqlite-blocks-v1']
            assert {copy.key for copy in copies} == {key for key in gets if key.startswith('copies/')}
            assert manager.last_timings['downloaded_bytes'] == sum(copy.size_bytes for copy in copies)
            assert manager.last_timings['reused_bytes'] == 0
            assert (target / 'chunks.sqlite').is_file()
            assert (target / 'keyword.sqlite').is_file()
    finally:
        for name in ('manifest', 'chunks', 'keyword', 'catalog', 'change_log'):
            getattr(deps, name).close()
