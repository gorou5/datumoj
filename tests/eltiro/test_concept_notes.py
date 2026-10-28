"""Concept notes have independent storage and always require model provenance."""

import pytest

from datumoj.eltiro.concept_notes import generate_concept_notes, list_concept_notes
from datumoj.eltiro.contracts import RerunCacheMiss
from datumoj.eltiro.models import ConsumerConfig, ConsumersConfig
from datumoj.eltiro.routing import SQLiteModelRouter
from datumoj.eltiro.testing.fakes import FakeModelCache, FakeModelProvider, FakeProfileStore
from datumoj.eltiro.testing.samples import make_consumer, make_demand, make_license, make_profile, make_routing_policy
from datumoj.rikolto.core.models import GenerationToken


@pytest.mark.parametrize("output,expected", [
    ('{"note":"One concept sentence."}', 1), ('{"note":"One. Two."}', 0),
    ('{"note":""}', 0), ('{"note":42}', 0), ('{"note":"Fine.","extra":1}', 0), ("invalid", 0),
])
def test_note_output_validation_and_cache_only_restart(tmp_path, output, expected):
    consumer = ConsumerConfig.model_validate({
        "id": "example-consumer", "inbox": "inbox", "outbox": "outbox", "allowed_sources": ["intl-wdi"],
        "models": make_consumer(authorized_sources=["intl-wdi"]),
        "max_active_demands": 1, "max_calls_per_run": 1})
    demand = make_demand(models={"max_tier": "high", "allow_cloud": True, "max_calls_per_run": 1})
    profiles, cache, provider = FakeProfileStore(), FakeModelCache(), FakeModelProvider(output)
    profile = make_profile(descriptions={"en": "Earlier generated text"}, model_generated_fields=["descriptions"])
    profiles.write_generation(1, [profile])
    options = dict(demand=demand, profiles=profiles, consumer=consumer, data_root=tmp_path,
                   cache=cache, providers={"cloud_api": provider}, licenses={"intl-wdi": make_license()},
                   policy=make_routing_policy(), generation_token=GenerationToken(
                       generation=1, manifest_sha256="ab" * 32))
    consumers = ConsumersConfig(consumers=[consumer])
    with SQLiteModelRouter(tmp_path, consumers=consumers) as router:
        notes = generate_concept_notes(1, router=router, **options)
        assert len(notes) == expected and len(provider.calls) == 1
    with SQLiteModelRouter(tmp_path, consumers=consumers) as router:
        assert generate_concept_notes(1, router=router, **options) == notes
        assert list_concept_notes(1, data_root=tmp_path) == notes
        assert profiles.list(1) == [profile]
        with pytest.raises(RerunCacheMiss):
            generate_concept_notes(1, router=router, **{**options, "cache": FakeModelCache()})
    assert len(provider.calls) == 1
