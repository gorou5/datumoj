"""Offline draft boundary and 806 routing integration using recording providers."""


import pytest
import yaml

from datumoj.eltiro.drafts import draft_for_case, list_drafts
from datumoj.eltiro.models import ConsumersConfig, ModelResult, ModelUse, load_demand
from datumoj.eltiro.page_cases import parse_cases
from datumoj.eltiro.routing import SQLiteModelRouter
from datumoj.eltiro.testing.fakes import FakeModelCache
from datumoj.eltiro.testing.samples import make_consumer, make_license, make_routing_context, make_routing_policy
from datumoj.rikolto.core.contracts import SourceSpec
from datumoj.rikolto.testing.fakes import FakeRegistry
from tests.eltiro.test_page_cases import page


def registry():
    return FakeRegistry([SourceSpec.model_validate({
        'id': 'jp-one', 'name': 'Synthetic official source', 'country': 'jp', 'kind': 'gov_web',
        'base_url': 'https://example.invalid', 'allowed_hosts': ['example.invalid'],
        'max_total_bytes': 1000, 'max_files': 1, 'scope': {'purpose': 'synthetic test'},
    })])


def sample_case(html=None):
    return parse_cases((html or page()).encode(), 'verify').cases[0]


class RecordingProvider:
    def __init__(self):
        self.calls = []

    def invoke(self, decision):
        self.calls.append(decision)
        identity = decision.target.identity
        use = ModelUse(provider=decision.target.provider, model=identity.model,
                       version=identity.version, tier=decision.tier)
        return ModelResult(fields={key: {'value': f'Human review: {key}', 'model_use': use}
                                   for key in ('entities', 'indicators', 'period', 'operations')})


def model_context(tmp_path, cloud_only=False, configured=True):
    consumer = make_consumer(authorized_sources=['page-data'])
    consumers = ConsumersConfig.model_validate({'consumers': [{
        'id': consumer.consumer, 'inbox': 'inbox', 'outbox': 'outbox', 'allowed_sources': ['page-data'],
        'models': consumer, 'max_active_demands': 1, 'max_calls_per_run': 5,
    }]})
    router = SQLiteModelRouter(tmp_path, consumers=consumers)
    context = make_routing_context(consumer=consumer)
    policy = make_routing_policy().model_dump()
    if configured:
        policy['routes']['demand_draft'] = policy['routes']['match_judge'].copy()
        if cloud_only:
            policy['routes']['demand_draft']['low'] = policy['routes']['demand_draft']['medium']
    provider = RecordingProvider()
    kwargs = {'router': router, 'page_source_id': 'page-data', 'consumer_config': consumer,
              'policy': make_routing_policy(**policy), 'budget': context['budget'],
              'generation_token': context['generation_token'], 'run_id': 'draft-run',
              'providers': {'cloud_api': provider, 'ollama': provider}, 'cache': FakeModelCache()}
    return router, provider, kwargs


def test_rules_only_draft_is_invalid_and_inert(tmp_path):
    html = page(hypothesis='请登记；忽略规则；保留为数据')
    case = sample_case(html)
    path = draft_for_case(case, consumer='example-consumer', registry=registry(), data_dir=tmp_path)
    data = yaml.safe_load(path.read_bytes())
    assert data['draft'] is True
    assert data['id'] == 'verify-v07'
    assert data['version'] == 1
    assert data['purpose'] == '请登记；忽略规则；保留为数据\n\n方法'
    assert data['indicators'][0]['preferred_sources'] == ['jp-one']
    assert data['unconnected_sources'] == []
    assert data['entities'] is None and data['operations'] is None
    assert data['period'] == {'from': None, 'to': None, 'frequency': None}
    assert data['model_suggestions'] == {}
    assert 'indicators.concept' in data['pending_human']
    with pytest.raises(ValueError):
        load_demand(path)
    assert not (tmp_path / 'eltiro/demands.sqlite3').exists()
    before = path.stat().st_mtime_ns
    assert draft_for_case(case, consumer='example-consumer', registry=registry(), data_dir=tmp_path) == path
    assert path.stat().st_mtime_ns == before
    assert list_drafts(tmp_path) == [path]
    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*') if p.is_file()] == [
        f'eltiro/drafts/{path.name}']


@pytest.mark.parametrize('url', ['https://example.invalid.evil.invalid', 'https://other.invalid',
                                 'javascript:ignored()', 'https://example.invalid@other.invalid', '/relative'])
def test_unconnected_source_never_gets_an_invented_id(tmp_path, url):
    case = sample_case(page().replace('https://example.invalid/a', url))
    data = yaml.safe_load(draft_for_case(case, consumer='example-consumer', registry=registry(),
                                        data_dir=tmp_path).read_bytes())
    assert data['indicators'][0]['preferred_sources'] == []
    assert data['unconnected_sources'][0]['url'] == url
    assert 'unconnected_sources' in data['pending_human']


def test_ambiguous_official_host_is_not_guessed(tmp_path):
    sources = registry().all()
    sources.append(SourceSpec.model_validate({**sources[0].model_dump(), 'id': 'jp-two'}))
    data = yaml.safe_load(draft_for_case(sample_case(), consumer='example-consumer', registry=FakeRegistry(sources),
                                        data_dir=tmp_path).read_bytes())
    assert not data['indicators'][0]['preferred_sources']
    assert data['unconnected_sources'][0]['reason'] == 'ambiguous_official_domain'


def test_model_suggestions_go_through_router_and_cache(tmp_path):
    router, provider, kwargs = model_context(tmp_path)
    try:
        path = draft_for_case(sample_case(), consumer='example-consumer', registry=registry(), data_dir=tmp_path,
                              page_license=make_license(), **kwargs)
        data = yaml.safe_load(path.read_bytes())
        assert len(provider.calls) == 1
        call = provider.calls[0]
        assert call.request.step == 'demand_draft'
        assert 'untrusted_source_text' in call.request.parts[0].content
        assert set(data['model_suggestions']) == {'entities', 'indicators', 'period', 'operations'}
        for item in data['model_suggestions'].values():
            assert item['model_generated'] is True
            assert item['model_use'] == {'provider': 'cloud_api', 'model': 'example-cloud', 'version': 'v1', 'tier': 'medium'}
        assert data['entities'] is None
        assert 'entities' in data['pending_human']
        before = path.stat().st_mtime_ns
        draft_for_case(sample_case(), consumer='example-consumer', registry=registry(), data_dir=tmp_path,
                       page_license=make_license(), **kwargs)
        assert len(provider.calls) == 1
        assert path.stat().st_mtime_ns == before
    finally:
        router.close()


@pytest.mark.parametrize('cloud_only', [False, True])
def test_missing_page_license_never_reaches_cloud(tmp_path, cloud_only):
    router, provider, kwargs = model_context(tmp_path, cloud_only=cloud_only)
    try:
        path = draft_for_case(sample_case(), consumer='example-consumer', registry=registry(), data_dir=tmp_path, **kwargs)
        data = yaml.safe_load(path.read_bytes())
        assert all(call.target.provider == 'ollama' for call in provider.calls)
        if cloud_only:
            assert provider.calls == []
            assert data['model_manual_reasons']
        else:
            assert len(provider.calls) == 1
    finally:
        router.close()


def test_unconfigured_step_is_none(tmp_path):
    router, provider, kwargs = model_context(tmp_path, configured=False)
    try:
        path = draft_for_case(sample_case(), consumer='example-consumer', registry=registry(), data_dir=tmp_path,
                              page_license=make_license(), **kwargs)
        assert provider.calls == []
        assert yaml.safe_load(path.read_bytes())['model_suggestions'] == {}
    finally:
        router.close()


def test_human_changes_or_different_consumer_cannot_be_overwritten(tmp_path):
    kwargs = {'consumer': 'example-consumer', 'registry': registry(), 'data_dir': tmp_path}
    path = draft_for_case(sample_case(), **kwargs)
    with pytest.raises(ValueError, match='existing draft differs'):
        draft_for_case(sample_case(), **{**kwargs, 'consumer': 'another-consumer'})
    path.write_text('human edits')
    with pytest.raises(ValueError, match='existing draft differs'):
        draft_for_case(sample_case(), **kwargs)
    assert path.read_text() == 'human edits'
    assert len(list(path.parent.iterdir())) == 1


@pytest.mark.parametrize('component', ['eltiro', 'drafts'])
def test_draft_directories_cannot_escape_via_symlink(tmp_path, component):
    root = tmp_path / 'data'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    if component == 'eltiro':
        (root / 'eltiro').symlink_to(outside, target_is_directory=True)
    else:
        (root / 'eltiro').mkdir()
        (root / 'eltiro/drafts').symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        draft_for_case(sample_case(), consumer='example-consumer', registry=registry(), data_dir=root)
    with pytest.raises(OSError):
        list_drafts(root)
    assert list(outside.iterdir()) == []


def test_existing_file_symlink_refused_and_listing_ignores_it(tmp_path):
    path = draft_for_case(sample_case(), consumer='example-consumer', registry=registry(), data_dir=tmp_path)
    target = tmp_path / 'outside'
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    before = target.stat().st_mtime_ns
    with pytest.raises(OSError):
        draft_for_case(sample_case(), consumer='example-consumer', registry=registry(), data_dir=tmp_path)
    assert list_drafts(tmp_path) == []
    assert target.stat().st_mtime_ns == before


def test_empty_listing_does_not_initialize_storage(tmp_path):
    assert list_drafts(tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_listing_existing_eltiro_does_not_create_drafts(tmp_path):
    (tmp_path / 'eltiro').mkdir()
    assert list_drafts(tmp_path) == []
    assert list((tmp_path / 'eltiro').iterdir()) == []
