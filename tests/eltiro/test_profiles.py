"""Source metadata is retained, never translated or inferred from numeric values."""

import json
from datetime import date
from pathlib import Path

import pytest

from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.profiles import generate_profiles
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeChunkStore, FakeObservationStore, FakeRegistry
from datumoj.rikolto.testing.samples import make_dataset, make_observation, make_spec


@pytest.fixture
def inputs(tmp_path):
    with SQLiteProfileStore(tmp_path) as store:
        yield {"observations": FakeObservationStore(), "catalog": FakeCatalogStore(), "chunks": FakeChunkStore(),
                   "registry": FakeRegistry([make_spec("jp-stat")]), "store": store}


def test_series_dimensions_coverage_and_source_scale(inputs):
    metadata = {"language": "ja", "methodology": "出典の方法", "concept_ids": [
        {"scheme": "e-Stat", "code": "000001"}], "scale": {"multiplier": 1000.0, "evidence": "出典: 倍率1000"}}
    inputs["catalog"].upsert([make_dataset("jp-stat:pop", "人口", description="出典の説明", extras=metadata)])
    rows = [
        make_observation("one", "123", indicator_code="POP", dims={"sex": "all"}),
        make_observation("two", None, note="秘匿", indicator_code="POP", dims={"sex": "all"},
                         area_code="27000", time_start=date(2021, 1, 1), time_end=date(2021, 12, 31)),
        make_observation("three", "100", indicator_code="POP", dims={"sex": "female"}),
    ]
    inputs["observations"].write_table("jp-stat", "t1", rows)
    profiles = generate_profiles(1, **inputs)
    assert len(profiles) == 2
    profile = next(p for p in profiles if p.series.fixed_dimensions == {"sex": "all"})
    assert profile.names == {"ja": "人口"}
    assert profile.descriptions == {"ja": "出典の説明"}
    assert [(c.scheme, c.code) for c in profile.concept_ids] == [("jp-stat", "POP"), ("e-Stat", "000001")]
    assert profile.areas == ["13000", "27000"]
    assert (profile.time_start, profile.time_end) == (date(2020, 1, 1), date(2021, 12, 31))
    assert profile.frequency == "annual" and profile.unit == "人" and profile.survey == "国勢調査"
    assert profile.scale.multiplier == 1000.0 and profile.scale.evidence == "出典: 倍率1000"
    assert profile.methodology == "出典の方法"
    assert all(p.model_generated_fields == [] for p in profiles)
    assert inputs["observations"].query_readonly("SELECT * FROM observations").provenance == rows


def test_multilingual_catalog_and_recorded_source_text(inputs):
    # Fixture provenance/license: tests/fixtures/ckan_egov/README.md (e-Gov metadata).
    path = Path("tests/fixtures/ckan_egov/package_show_cao_syukujitsu.json")
    raw = json.loads(path.read_bytes())["result"]
    dataset = make_dataset("jp-stat:holidays", raw["title"], description=raw["notes"], extras={"language": "ja"})
    translated = make_dataset("jp-stat:other", "Source title", description="Source notes", extras={
        "language": "en", "title_translated": {"ja": "出典の名前", "fr": "Nom source"},
        "notes_translated": {"ja": "出典の説明", "fr": "Description source"},
    })
    inputs["catalog"].upsert([dataset, translated])
    profiles = generate_profiles(1, **inputs)
    holiday = next(p for p in profiles if p.names.get("ja") == raw["title"])
    assert holiday.descriptions == {"ja": raw["notes"]}
    profile = next(p for p in profiles if p.names.get("en") == "Source title")
    assert profile.names == {"en": "Source title", "ja": "出典の名前", "fr": "Nom source"}
    assert profile.descriptions == {"en": "Source notes", "ja": "出典の説明", "fr": "Description source"}
    assert all(p.availability == "catalog_only" and p.model_generated_fields == [] for p in profiles)


def test_catalog_enumeration_is_not_top_k_only(inputs):
    inputs["catalog"].upsert([make_dataset(f"jp-stat:item-{i}", f"Name {i}") for i in range(130)])
    profiles = generate_profiles(1, **inputs)
    assert len(profiles) == 130
    assert all(set(p.names) == {"und"} and p.frequency == "irregular" for p in profiles)


def test_conflicting_units_fail_without_replacing_generation(inputs):
    first = make_observation("one", "1")
    inputs["observations"].write_table("jp-stat", "t1", [first])
    previous = generate_profiles(1, **inputs)
    inputs["observations"].write_table("jp-stat", "t1", [first, make_observation("two", "2", unit="千人")])
    with pytest.raises(ValueError, match="conflicting source units"):
        generate_profiles(1, **inputs)
    assert inputs["store"].list(1) == previous


def test_empty_inputs_clear_target_and_keep_other_generations(inputs):
    inputs["observations"].write_table("jp-stat", "t1", [make_observation("one", "1")])
    previous = generate_profiles(1, **inputs)
    generate_profiles(2, **inputs, previous_generation=1)
    inputs["observations"].write_table("jp-stat", "t1", [])
    assert generate_profiles(2, **inputs, previous_generation=1) == []
    assert inputs["store"].list(1) == previous


def test_unregistered_source_fails_without_writing(inputs):
    inputs["catalog"].upsert([make_dataset("jp-other:item", "Source title")])
    with pytest.raises(ValueError, match="unregistered profile source"):
        generate_profiles(1, **inputs)
    assert inputs["store"].list(1) == []


def test_existing_parser_concept_codes_and_indicator_translations(inputs):
    inputs["catalog"].upsert([make_dataset("jp-stat:pop", "人口", extras={
        "stat_inf_id": "000000000001", "indicator_code": "POP", "language": "ja",
        "title_translated": {"en": "Population"}, "notes_translated": {"en": "Source description"},
    })])
    row = make_observation("one", "1", time_start=None, time_end=None, indicator_code="POP")
    inputs["observations"].write_table("jp-stat", "t1", [row])
    profile, = generate_profiles(1, **inputs)
    assert profile.names == {"ja": "人口", "en": "Population"}
    assert {(c.scheme, c.code) for c in profile.concept_ids} == {("e-Stat", "000000000001"), ("jp-stat", "POP")}
    assert profile.time_start is None and profile.time_end is None
