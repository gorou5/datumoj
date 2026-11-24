"""Source metadata is retained, never translated or inferred from numeric values."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from datumoj.eltiro.models import SeriesIdentity
from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.profiles import _profile, generate_profiles
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


@pytest.mark.parametrize("count,listed", [(256, True), (257, False)])
def test_areas_over_the_cap_are_not_listed_and_other_series_are_unaffected(inputs, count, listed):
    inputs["observations"].write_table("jp-stat", "wide", [
        make_observation(f"w{i}", "1", table_key="wide", indicator_code="WIDE", area_code=f"A{i:04d}")
        for i in range(count)])
    inputs["observations"].write_table("jp-stat", "narrow", [
        make_observation("n1", "1", table_key="narrow", indicator_code="NARROW")])
    profiles = {p.series.table_key: p for p in generate_profiles(1, **inputs)}
    assert profiles["wide"].areas == (sorted(f"A{i:04d}" for i in range(count)) if listed else [])
    assert profiles["narrow"].areas == ["13000"]


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


def test_profile_reads_use_the_batch_timeout_and_a_timeout_still_propagates(inputs):
    from datumoj.eltiro.contracts import BATCH_QUERY_TIMEOUT_S
    from datumoj.rikolto.core.contracts import QueryTimeout

    class Timed:
        def __init__(self, backing, seconds):
            self.backing, self.seconds, self.timeouts = backing, seconds, []

        def describe(self):
            return self.backing.describe()

        def query_readonly(self, sql, max_rows=1000, timeout_s=10, **kwargs):
            self.timeouts.append(timeout_s)
            if self.seconds > timeout_s:
                raise QueryTimeout("timeout")
            return self.backing.query_readonly(sql, max_rows, timeout_s, **kwargs)

    inputs["catalog"].upsert([make_dataset("jp-stat:pop", "人口")])
    inputs["observations"].write_table("jp-stat", "t1", [make_observation("one", "1", indicator_code="POP")])
    backing = inputs["observations"]
    ok = Timed(backing, BATCH_QUERY_TIMEOUT_S - 1)
    generate_profiles(1, **{**inputs, "observations": ok})
    assert len(ok.timeouts) == 2 and set(ok.timeouts) == {BATCH_QUERY_TIMEOUT_S}
    with pytest.raises(QueryTimeout):
        generate_profiles(2, **{**inputs, "observations": Timed(backing, BATCH_QUERY_TIMEOUT_S + 1)})


# 324: real data over the model caps is cut to them; a unit that still fails can be skipped on request.
def _langs(count):
    return [f"l{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(count)]


def test_over_cap_texts_and_lists_are_cut_and_other_profiles_stay_verbatim(inputs):
    langs = _langs(33)
    concepts = [{"scheme": "x", "code": f"c{i:03d}"} for i in range(300)]
    inputs["catalog"].upsert([
        make_dataset("jp-stat:wide", "Wide", description="d" * 5000, extras={
            "language": "ja", "concept_ids": concepts, "methodology": "m" * 5000, "unit": "u" * 300,
            "title_translated": {**{lang: "n" * 300 for lang in langs}, "en": "short name"},
            "notes_translated": {lang: "t" * 5000 for lang in langs}}),
        make_dataset("jp-stat:plain", "Plain", description="plain text", extras={"language": "ja"}),
    ])
    profiles = {p.series.table_key: p for p in generate_profiles(1, **inputs)}
    wide, plain = profiles["catalog:jp-stat:wide"], profiles["catalog:jp-stat:plain"]
    # 33 codes + "en" + "ja" = 35 languages; kept: the first 32 codes in sorted order.
    assert sorted(wide.names) == sorted([*langs, "en", "ja"])[:32]
    assert all(len(v) <= 256 for v in wide.names.values()) and wide.names["en"] == "short name"
    assert wide.names[langs[0]] == "n" * 256
    assert len(wide.descriptions) == 32 and all(len(v) == 4096 for v in wide.descriptions.values())
    assert wide.methodology == "m" * 4096 and wide.unit == "u" * 256
    assert [c.code for c in wide.concept_ids] == [f"c{i:03d}" for i in range(256)]
    assert wide.series.indicator == "Wide"
    assert plain.names == {"ja": "Plain"} and plain.descriptions == {"ja": "plain text"}
    assert plain.concept_ids == [] and plain.methodology is None and plain.unit is None


def test_an_exactly_capped_text_is_unchanged_and_a_blank_one_is_dropped(inputs):
    inputs["catalog"].upsert([make_dataset("jp-stat:edge", "Edge", description="e" * 4096, extras={
        "language": "ja", "title_translated": {"en": "n" * 256, "fr": "   "}, "methodology": "   ", "unit": " "})])
    (profile,) = generate_profiles(1, **inputs)
    assert profile.names == {"en": "n" * 256, "ja": "Edge"} and profile.descriptions == {"ja": "e" * 4096}
    assert profile.methodology is None and profile.unit is None


def test_names_that_end_up_empty_fall_back_to_the_indicator(inputs):
    series = SeriesIdentity(source_id="jp-stat", table_key="t", indicator="x" * 256)
    profile = _profile(series, names={"en": "  "}, frequency="irregular", availability="catalog_only", generation=1)
    assert profile.names == {"und": "x" * 256}
    # Through the public entry: the translation under the dataset's own language is blank after the cut.
    inputs["catalog"].upsert([make_dataset("jp-stat:t", "T", extras={"language": "ja", "title_translated": {
        "ja": " " * 300}})])
    (profile,) = generate_profiles(1, **inputs)
    assert profile.names == {"und": "T"}


def _conflicting(inputs):
    inputs["observations"].write_table("jp-stat", "bad", [
        make_observation("b1", "1", table_key="bad"), make_observation("b2", "2", table_key="bad", unit="千人")])
    inputs["observations"].write_table("jp-stat", "good", [make_observation("g1", "1", table_key="good")])


def test_conflicts_still_fail_by_default_and_skip_only_when_asked(inputs):
    _conflicting(inputs)
    with pytest.raises(ValueError, match="conflicting source units"):
        generate_profiles(1, **inputs)
    assert inputs["store"].list(1) == []
    skipped = []
    profiles = generate_profiles(1, **inputs, skipped=skipped)
    assert [p.series.table_key for p in profiles] == ["good"]
    assert skipped == [{"unit": json.dumps(["observations", "jp-stat", "bad"], separators=(",", ":")),
                        "error": "conflict", "fields": ["units"]}]
    assert [p.series.table_key for p in inputs["store"].list(1)] == ["good"]


def test_validation_errors_are_reported_by_location_without_input(inputs):
    inputs["catalog"].upsert([make_dataset("jp-stat:bad", "Bad", extras={"language": "ja", "frequency": "sometimes"}),
                              make_dataset("jp-stat:fine", "Fine")])
    with pytest.raises(ValueError):
        generate_profiles(1, **inputs)
    skipped = []
    assert [p.series.indicator for p in generate_profiles(1, **inputs, skipped=skipped)] == ["Fine"]
    (item,) = skipped
    assert item["error"] == "ValidationError" and item["fields"] and "sometimes" not in json.dumps(item)


def test_a_skipped_unit_is_not_cached_and_is_retried_next_generation(inputs):
    _conflicting(inputs)
    skipped = []
    generate_profiles(1, **inputs, skipped=skipped)
    assert len(skipped) == 1
    # Same data: the good unit comes from the cache, the skipped one is attempted (and reported) again.
    again = []
    profiles = generate_profiles(2, **inputs, previous_generation=1, skipped=again)
    assert again == skipped and [p.series.table_key for p in profiles] == ["good"]
    inputs["observations"].write_table("jp-stat", "bad", [make_observation("b1", "1", table_key="bad")])
    retry = []
    profiles = generate_profiles(3, **inputs, previous_generation=2, skipped=retry)
    assert retry == [] and sorted(p.series.table_key for p in profiles) == ["bad", "good"]


def test_texts_that_are_blank_only_after_the_cut_are_dropped(inputs):
    inputs["catalog"].upsert([make_dataset("jp-stat:cut", "Cut", description=" " * 4096 + "tail", extras={
        "language": "ja", "title_translated": {"en": " " * 300 + "tail"}, "methodology": " " * 5000 + "tail"})])
    (profile,) = generate_profiles(1, **inputs)
    assert profile.descriptions == {} and profile.methodology is None and profile.names == {"ja": "Cut"}


def test_generation_level_errors_are_never_skipped(inputs):
    inputs["catalog"].upsert([make_dataset("jp-other:item", "Source title")])
    with pytest.raises(ValueError, match="unregistered profile source"):
        generate_profiles(1, **inputs, skipped=[])
    assert inputs["store"].list(1) == []

    class NoProvenance:
        def __init__(self, backing):
            self.backing = backing

        def describe(self):
            return self.backing.describe()

        def query_readonly(self, sql, *args, **kwargs):
            result = self.backing.query_readonly(sql, *args, **kwargs)
            if sql.startswith("SELECT * "):
                return SimpleNamespace(columns=result.columns, rows=result.rows, provenance=[])
            return result

    class Orphan:
        def iter_chunks(self):
            return [SimpleNamespace(doc_key="jp-stat:doc", snapshot_sha256="ab" * 32)]

        def get_document(self, *key):
            return None

    inputs["catalog"] = FakeCatalogStore()
    with pytest.raises(ValueError, match="chunk has no source document"):
        generate_profiles(1, **{**inputs, "chunks": Orphan()}, skipped=[])
    inputs["observations"].write_table("jp-stat", "t1", [make_observation("one", "1")])
    with pytest.raises(ValueError, match="complete observation provenance"):
        generate_profiles(1, **{**inputs, "observations": NoProvenance(inputs["observations"])}, skipped=[])
    assert inputs["store"].list(1) == []
