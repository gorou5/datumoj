"""Source metadata is retained, never translated or inferred from numeric values."""

import json
from datetime import date
from pathlib import Path

import pytest

from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.profiles import generate_profiles
from datumoj.rikolto.parsers.eurostat import EurostatParser, read_structure
from datumoj.rikolto.parsers.oecd_sdmx import OecdSdmxParser
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeChunkStore, FakeObservationStore, FakeRegistry
from datumoj.rikolto.testing.samples import make_dataset, make_observation, make_snapshot, make_spec


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


def test_parser_declared_scales_reach_profiles_and_mixed_datasets_stay_unconfirmed(inputs):
    # 317: recorded samples through the real parsers; the scale is whatever the parser declared (or nothing).
    fixtures = Path("tests/fixtures")
    oecd = fixtures / "oecd_sdmx/namain10_JPN-USA_2020-2024.csv"
    snapshot = make_snapshot("intl-oecd", "https://sdmx.oecd.org/public/rest/data/test", oecd.read_bytes())
    parsed = [("intl-oecd", OecdSdmxParser().parse(snapshot, oecd.read_bytes(), {}))]
    for code, name in (("gov_10dd_edpt1", "gov_10dd_edpt1_EL_2000-2015.xml"),
                       ("nama_10_gdp", "nama_10_gdp_DE-FR-NL_2020-2024.xml")):
        data = (fixtures / "eurostat" / name).read_bytes()
        structure = read_structure((fixtures / "eurostat" / f"datastructure_{code.upper()}.xml").read_bytes(), code)
        parsed.append(("eu-eurostat", EurostatParser().parse(
            make_snapshot("eu-eurostat", "https://ec.europa.eu/eurostat/test", data), data, {"structure": structure})))
    for source_id, result in parsed:
        inputs["catalog"].upsert(result.datasets)
        for table_key in sorted({o.table_key for o in result.observations}):
            inputs["observations"].write_table(source_id, table_key, [o for o in result.observations if o.table_key == table_key])
    inputs["registry"] = FakeRegistry([make_spec("intl-oecd"), make_spec("eu-eurostat")])
    scales = {(p.series.source_id, p.series.table_key): p.scale for p in generate_profiles(1, **inputs)}
    assert scales["intl-oecd", "df_table1_expenditure"].multiplier == 1e6
    assert "UNIT_MULT=6" in scales["intl-oecd", "df_table1_expenditure"].evidence
    assert scales["eu-eurostat", "nama_10_gdp"].multiplier == 1e6
    assert scales["eu-eurostat", "gov_10dd_edpt1"].multiplier is None  # PC_GDP and MIO_EUR mixed: unconfirmed


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


@pytest.mark.parametrize("code_labels, names", [
    ({"na_item": {"B1GQ": "Gross domestic product at market prices"}}, {"en": "Gross domestic product at market prices", "und": "B1GQ"}),
    ({"na_item": {"GD": "Government consolidated gross debt"}}, {"und": "B1GQ"}),  # no name for this code: as before
    ({}, {"und": "B1GQ"}),
    ({"MEASURE": {"B1GQ": "Another dimension's B1GQ"}}, {"und": "B1GQ"}),  # the code sits in na_item, not MEASURE
])
def test_indicator_name_from_source_code_labels_keeps_series_identity(inputs, code_labels, names):
    row = make_observation("e1", "1", source_id="eu-eurostat", dataset_key="eu-eurostat:nama_10_gdp",
                           table_key="nama_10_gdp", indicator="B1GQ", indicator_code="B1GQ", unit="CP_MEUR",
                           dims={"na_item": "B1GQ", "geo": "EL"})
    inputs["registry"] = FakeRegistry([make_spec("eu-eurostat")])
    inputs["catalog"].upsert([make_dataset("eu-eurostat:nama_10_gdp", "NAMA_10_GDP data structure",
                                           extras={"code_labels": code_labels})])
    inputs["observations"].write_table("eu-eurostat", "nama_10_gdp", [row])
    profile, = generate_profiles(1, **inputs)
    assert profile.names == names
    assert (profile.series.indicator, profile.series.indicator_code) == ("B1GQ", "B1GQ")


def test_own_time_period_is_not_part_of_series_identity(inputs):
    rows = [make_observation(f"y{year}", "1", indicator_code="POP", time_label=str(year),
                             time_start=date(year, 1, 1), time_end=date(year, 12, 31),
                             dims={"sex": "all", "TIME_PERIOD": str(year)}) for year in (2020, 2021, 2022)]
    inputs["observations"].write_table("jp-stat", "t1", rows)
    profiles = generate_profiles(1, **inputs)
    assert len(profiles) == 1
    assert profiles[0].series.fixed_dimensions == {"sex": "all"}
    assert (profiles[0].time_start, profiles[0].time_end) == (date(2020, 1, 1), date(2022, 12, 31))


def test_time_period_that_differs_from_the_row_period_stays_in_identity(inputs):
    # Not the row's own period (e.g. a base period): never merged away silently.
    rows = [make_observation(f"b{base}", "1", indicator_code="IDX", time_label="2022",
                             time_start=date(2022, 1, 1), time_end=date(2022, 12, 31),
                             dims={"TIME_PERIOD": base}) for base in ("2015", "2020")]
    inputs["observations"].write_table("jp-stat", "t1", rows)
    profiles = generate_profiles(1, **inputs)
    assert sorted(p.series.fixed_dimensions["TIME_PERIOD"] for p in profiles) == ["2015", "2020"]


def test_profiles_cached_under_an_older_identity_rule_are_rebuilt(inputs, monkeypatch):
    from datumoj.eltiro import profiles as module

    rows = [make_observation(f"y{year}", "1", indicator_code="POP", time_label=str(year),
                             time_start=date(year, 1, 1), time_end=date(year, 12, 31),
                             dims={"TIME_PERIOD": str(year)}) for year in (2020, 2021)]
    inputs["observations"].write_table("jp-stat", "t1", rows)
    with monkeypatch.context() as old:
        # Generation 1 as written before the rule existed: TIME_PERIOD kept, no rule salt.
        old.setattr(module, "series_dimensions", lambda dims, _label: dict(dims))
        old.setattr(module, "SERIES_IDENTITY_RULE", "series-identity/1")
        assert len(generate_profiles(1, **inputs)) == 2
    # Same table bytes, incremental run: the old split identities must not be reused.
    profiles = generate_profiles(2, **inputs, previous_generation=1)
    assert [p.series.fixed_dimensions for p in profiles] == [{}]
