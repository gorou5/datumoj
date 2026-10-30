"""Small output-boundary and difficulty tests independent of live models."""

import json

import pytest

from datumoj.eltiro.model_match import _answer, _model_config, _one_sentence, judge_candidates
from datumoj.eltiro.models import ConsumerConfig, ConsumersConfig, ModelResult
from datumoj.eltiro.routing import SQLiteModelRouter
from datumoj.eltiro.testing.fakes import FakeModelCache, FakeModelProvider
from datumoj.eltiro.testing.samples import (
    make_consumer,
    make_demand,
    make_license,
    make_match,
    make_profile,
    make_route_decision,
    make_routing_policy,
    make_series,
)
from datumoj.rikolto.core.models import GenerationToken


@pytest.mark.parametrize("text,expected", [
    ("One sentence.", True), ("一句说明。", True), ("Value 1.5 units.", True),
    ("", False), ("  ", False), ("one\ntwo", False), ("One. Two.", False),
    ("一。二。", False), ("x" * 513, False), (42, False),
])
def test_one_sentence_boundary(text, expected):
    assert _one_sentence(text) is expected


@pytest.mark.parametrize("text", [
    '{"verdict":"relevant","verdict":"irrelevant","reason":"One sentence."}',
    '["relevant", "reason"]', 'null', '{"verdict":"relevant","reason":"Fine.","extra":0}',
])
def test_strict_output_object(text):
    decision = make_route_decision()
    result = FakeModelProvider(text).invoke(decision)
    assert _answer(result, ("verdict", "reason")) is None


def test_unexpected_model_fields_are_uncertain():
    result = FakeModelProvider("{}").invoke(make_route_decision())
    result = ModelResult(fields={"unexpected": result.fields["answer"]})
    assert _answer(result, ("verdict", "reason")) is None


def test_disabled_model_retains_original_fingerprint():
    assert _model_config("original-803-808", False, None, None) == "original-803-808"
    with pytest.raises(ValueError):
        _model_config("original", "yes", None, None)


@pytest.mark.parametrize("complete,close,expected", [(False, False, "high"), (True, True, "high"),
                                                     (True, False, "medium")])
def test_difficulty_comes_from_candidates_and_confidence(tmp_path, complete, close, expected):
    consumer = ConsumerConfig.model_validate({
        "id": "example-consumer", "inbox": "inbox", "outbox": "outbox", "allowed_sources": ["intl-wdi"],
        "models": make_consumer(authorized_sources=["intl-wdi"]),
        "max_active_demands": 10, "max_calls_per_run": 10})
    demand = make_demand(models={"max_tier": "high", "allow_cloud": True, "max_calls_per_run": 10})
    profile = make_profile(unit="USD", methodology="definition" if complete else None, survey="survey")
    records = [make_match(confidence=0.8)]
    profiles = [profile]
    if close:
        other = make_series(table_key="second")
        profiles.append(profile.model_copy(update={"series": other}))
        records.append(make_match(series=other, confidence=0.79))
    provider = FakeModelProvider('{"verdict":"irrelevant","reason":"Different concept."}')
    with SQLiteModelRouter(tmp_path, consumers=ConsumersConfig(consumers=[consumer])) as router:
        result = judge_candidates(demand, 1, records, profiles=profiles, consumer=consumer, data_root=tmp_path,
                                  router=router, cache=FakeModelCache(), licenses={"intl-wdi": make_license()},
                                  policy=make_routing_policy(), providers={"cloud_api": provider},
                                  generation_token=GenerationToken(generation=1, manifest_sha256="ab" * 32))
    assert all(call.request.difficulty == expected for call in provider.calls)
    assert all(r.confidence == 0.1 and not r.auto_bind for r in result)
    assert all(json.loads(r.reason)["matcher_reason"] == records[0].reason for r in result)
    assert all(r.tier == "deterministic" for r in records)  # Caller input remains immutable.
