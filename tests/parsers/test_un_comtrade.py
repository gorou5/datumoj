"""UN Comtrade parser on the synthetic samples (tests/fixtures/comtrade; no real Comtrade data is committed).

The sample files cover 2022–2023 in one response; ``data_bytes`` cuts them to one year, the way the crawler asks
(one reporter-partner-year per request). ``release_bytes`` rewrites the one-year getDA samples to another year.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError, discover_parsers
from datumoj.rikolto.core.models import TimeGranularity
from datumoj.rikolto.parsers.un_comtrade import UnComtradeParser, read_releases
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/comtrade"
BASE = "https://comtradeapi.un.org/"
RELEASE_842_2023 = "208422023012021@2024-03-01T00:00:00"


def release_url(reporter, year):
    return f"{BASE}public/v1/getDA/C/A/HS?reporterCode={reporter}&period={year}"


def data_url(reporter, partner, year):
    return (f"{BASE}data/v1/get/C/A/HS?reporterCode={reporter}&partnerCode={partner}&period={year}"
            "&flowCode=X,M&cmdCode=TOTAL&partner2Code=0&customsCode=C00&motCode=0")


def _doc(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _dump(doc):
    doc["count"] = len(doc["data"])
    return json.dumps(doc, indent=1).encode()


def data_doc(reporter, partner, year):
    """The synthetic response of one reporter-partner-year: the rows of that period only."""
    doc = _doc(f"data_{reporter}_{partner}_2022-2023.json")
    doc["data"] = [row for row in doc["data"] if row["period"] == str(year)]
    return doc


def data_bytes(reporter, partner, year):
    return _dump(data_doc(reporter, partner, year))


def release_doc(reporter, year, *, revised=False):
    """getDA of one reporter and year. Only 842 / 2023 has a recorded revision; other years reuse the 2023 shape."""
    doc = _doc("getDA_842_2023_revised.json" if revised else f"getDA_{reporter}_2023.json")
    for row in doc["data"]:
        row["period"] = year
        if year != 2023:
            row["datasetCode"] = int(str(row["datasetCode"]).replace("2023", str(year)))
    return doc


def release_bytes(reporter, year, *, revised=False):
    return _dump(release_doc(reporter, year, revised=revised))


def empty_release_bytes():
    return _dump({"elapsedTime": "0.01 secs", "count": 0, "data": [], "error": ""})


def releases(reporter=842, year=2023, *, revised=False):
    return read_releases(release_bytes(reporter, year, revised=revised), reporter, year)


def sample(reporter, partner, year, data=None, **meta):
    data = data_bytes(reporter, partner, year) if data is None else data
    meta = {"request_type": "data", "reporter": reporter, "partner": partner, "year": year,
            "releases": releases(reporter, year), **meta}
    return ParseSample(make_snapshot("intl-comtrade", data_url(reporter, partner, year), data), data, meta)


def parse(item, **override):
    meta = {**item.meta, **override}
    return UnComtradeParser().parse(item.snapshot, item.data, meta)


class TestUnComtradeParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return UnComtradeParser()

    @pytest.fixture
    def samples(self):
        return [sample(842, 156, 2023), sample(251, 276, 2022), sample(410, 392, 2023)]

    @pytest.fixture
    def bad_samples(self):
        row = data_doc(842, 156, 2023)["data"][0]
        return [
            b"not json", b"\x89PNG\r\n\x1a\n", b"[]", (FIXTURES / "error_401_missing_key.json").read_bytes(),
            b'{"data": [], "error": "boom"}', b'{"data": [{"reporterCode": NaN}]}',
            json.dumps({"data": [{**row, "primaryValue": "4000000000"}], "error": ""}).encode(),
            json.dumps({"data": [{**row, "reporterCode": None}], "error": ""}).encode(),
            json.dumps({"data": [row, row], "error": ""}).encode(),  # the same observation twice
        ]


def test_plugin_is_discovered():
    assert discover_parsers()[("un_comtrade", "*")] is UnComtradeParser


def test_total_rows_keep_primary_cif_and_fob_verbatim():
    parsed = parse(sample(842, 156, 2023))
    assert not parsed.warnings
    by_flow = {o.dims["flow"]: o for o in parsed.observations}
    assert set(by_flow) == {"X", "M"}
    export, imports = by_flow["X"], by_flow["M"]
    # Export: primaryValue = fobvalue, cifvalue is null in the source, so the key is absent (never 0).
    assert export.value == Decimal("4000000000.0") and str(export.value) == "4000000000.0"
    assert "cifvalue" not in export.dims and export.dims["fobvalue"] == "4000000000.0"
    # Import: primaryValue = cifvalue; both values are stored.
    assert imports.value == Decimal("5250000000.0")
    assert (imports.dims["cifvalue"], imports.dims["fobvalue"]) == ("5250000000.0", "5000000000.0")
    for obs in parsed.observations:
        assert obs.dims | {"cifvalue": "", "fobvalue": ""} == {
            "reporter": "842", "partner": "156", "partner2": "0", "flow": obs.dims["flow"], "year": "2023",
            "cmd": "TOTAL", "customs": "C00", "mot": "0", "classification": "H6", "cifvalue": "", "fobvalue": "",
            "datasetChecksum": "100842",
        }
        assert (obs.unit, obs.area_code, obs.time_label, obs.granularity) == ("USD", "842", "2023", TimeGranularity.year)
        assert (obs.time_start, obs.time_end) == (date(2023, 1, 1), date(2023, 12, 31))
        assert (obs.source_id, obs.table_key, obs.dataset_key) == (
            "intl-comtrade", "total_842_156_2023", "intl-comtrade:total_842_156_2023")
        assert obs.note is None
    assert (export.indicator, export.indicator_code, imports.indicator, imports.indicator_code) == (
        "Exports", "X", "Imports", "M")
    assert [o.locator for o in parsed.observations] == ["row=1", "row=2"]
    dataset, = parsed.datasets
    assert dataset.extras == {"reporter": 842, "partner": 156, "year": 2023, "cmdCode": "TOTAL"}


def test_release_comes_from_the_getda_record_in_the_meta():
    parsed = parse(sample(842, 156, 2023))
    assert {(o.release_id, o.released_at) for o in parsed.observations} == {(RELEASE_842_2023, date(2024, 3, 1))}
    revised = parse(sample(842, 156, 2023), releases=releases(842, 2023, revised=True))
    assert {(o.release_id, o.released_at) for o in revised.observations} == {
        ("2084220230120219@2024-06-01T00:00:00", date(2024, 6, 1))}
    assert {o.dims["datasetChecksum"] for o in revised.observations} == {"999999"}


def test_release_date_is_the_date_part_of_last_released_with_seven_fraction_digits():
    stamp = "2024-02-11T05:48:29.3133333"
    moved = [{**releases()[0], "lastReleased": stamp}]
    parsed = parse(sample(842, 156, 2023), releases=moved)
    assert {(o.release_id, o.released_at) for o in parsed.observations} == {
        (f"208422023012021@{stamp}", date(2024, 2, 11))}


@pytest.mark.parametrize("meta", [{}, {"releases": []}, {"releases": releases(842, 2022)}])
def test_without_a_matching_release_the_identity_stays_empty(meta):
    item = sample(842, 156, 2023)
    parsed = UnComtradeParser().parse(item.snapshot, item.data, meta)
    assert len(parsed.observations) == 2
    assert {(o.release_id, o.released_at) for o in parsed.observations} == {(None, None)}
    assert all("datasetChecksum" not in o.dims for o in parsed.observations)
    assert any("release_id 留空" in w for w in parsed.warnings)


def test_obs_id_follows_the_series_not_the_values_or_the_release():
    first = {o.dims["flow"]: o for o in parse(sample(842, 156, 2023)).observations}
    later_data = data_bytes(842, 156, 2023).replace(b"4000000000.0", b"4100000000.0")
    later = parse(sample(842, 156, 2023, data=later_data), releases=releases(842, 2023, revised=True))
    again = {o.dims["flow"]: o for o in later.observations}
    assert again["X"].value == Decimal("4100000000.0") and first["X"].value == Decimal("4000000000.0")
    assert {k: o.obs_id for k, o in first.items()} == {k: o.obs_id for k, o in again.items()}
    assert first["X"].obs_id != first["M"].obs_id
    assert first["X"].release_id != again["X"].release_id


def test_reporter_equal_to_partner_rows_are_excluded():
    item = sample(251, 276, 2022)
    assert sum(row["partnerCode"] == row["reporterCode"] for row in json.loads(item.data)["data"]) == 1
    parsed = parse(item)
    assert [(o.dims["reporter"], o.dims["partner"], o.dims["flow"]) for o in parsed.observations] == [
        ("251", "276", "X"), ("251", "276", "M")]
    assert any("reporter = partner" in w for w in parsed.warnings)


def test_france_and_the_united_states_use_comtrade_codes():
    parsed = parse(sample(276, 251, 2023))
    assert {(o.dims["reporter"], o.dims["partner"]) for o in parsed.observations} == {("276", "251")}
    us = parse(sample(842, 156, 2023))
    assert {o.area_code for o in us.observations} == {"842"}
    for item in (sample(276, 251, 2023), sample(842, 156, 2023), sample(251, 276, 2022)):
        assert not {"250", "840"} & {v for o in parse(item).observations for v in (o.area_code, o.dims["partner"])}


@pytest.mark.parametrize("field,value", [
    ("partner2Code", 156), ("customsCode", "C01"), ("motCode", 1000), ("cmdCode", "0101"), ("flowCode", "RX"),
    ("flowCode", ["X"]),
])
def test_only_total_rows_are_kept(field, value):
    doc = data_doc(842, 156, 2023)
    doc["data"][1][field] = value
    parsed = parse(sample(842, 156, 2023, data=_dump(doc)))
    assert [o.dims["flow"] for o in parsed.observations] == ["X"]
    assert any("不是合计行" in w for w in parsed.warnings)


def test_missing_values_stay_empty_with_a_note_and_never_become_zero():
    doc = data_doc(842, 156, 2023)
    doc["data"][0]["primaryValue"] = None
    parsed = parse(sample(842, 156, 2023, data=_dump(doc)))
    missing = next(o for o in parsed.observations if o.dims["flow"] == "X")
    assert missing.value is None and missing.note == "primaryValue=null"
    assert next(o for o in parsed.observations if o.dims["flow"] == "M").value == Decimal("5250000000.0")


def test_zero_is_kept_as_a_real_value():
    doc = data_doc(842, 156, 2023)
    doc["data"][0]["primaryValue"] = 0
    doc["data"][0]["fobvalue"] = 0
    export = next(o for o in parse(sample(842, 156, 2023, data=_dump(doc))).observations if o.dims["flow"] == "X")
    assert export.value == 0 and export.note is None and export.dims["fobvalue"] == "0"


def test_the_merged_two_year_sample_splits_into_one_table_per_year():
    item = sample(842, 156, 2023, data=(FIXTURES / "data_842_156_2022-2023.json").read_bytes())
    parsed = parse(item)
    assert len(parsed.observations) == 4
    assert {o.table_key for o in parsed.observations} == {"total_842_156_2022", "total_842_156_2023"}
    # 2022 has no getDA record in this meta, so only 2023 carries a release.
    assert {o.time_label: o.release_id for o in parsed.observations} == {"2022": None, "2023": RELEASE_842_2023}
    assert len(parsed.datasets) == 2


def test_getda_snapshot_only_marks_the_release():
    data = release_bytes(842, 2023)
    snapshot = make_snapshot("intl-comtrade", release_url(842, 2023), data)
    parsed = UnComtradeParser().parse(snapshot, data, {"request_type": "release"})
    assert not parsed.observations and not parsed.datasets and parsed.warnings


def test_empty_data_array_is_not_an_error():
    parsed = parse(sample(842, 156, 2023, data=empty_release_bytes()))
    assert not parsed.observations and parsed.warnings


def test_read_releases_selects_the_reporter_and_year():
    record, = releases()
    assert record == {"period": 2023, "classificationCode": "H6", "datasetCode": 208422023012021,
                      "datasetChecksum": 100842, "lastReleased": "2024-03-01T00:00:00"}
    data = release_bytes(842, 2023)
    assert read_releases(data, 842, 2022) == [] and read_releases(data, 156, 2023) == []
    assert read_releases(empty_release_bytes(), 842, 2023) == []
    assert releases(842, 2023, revised=True)[0]["datasetChecksum"] == 999999


def test_read_releases_rejects_bad_getda():
    doc = release_doc(842, 2023)
    for change in ({"datasetChecksum": None}, {"lastReleased": "yesterday"}, {"datasetCode": "x"}):
        broken = release_doc(842, 2023)
        broken["data"][0].update(change)
        with pytest.raises(ParseError):
            read_releases(_dump(broken), 842, 2023)
    doc["data"].append(dict(doc["data"][0]))  # two records for the same classification
    with pytest.raises(ParseError):
        read_releases(_dump(doc), 842, 2023)


@pytest.mark.parametrize("meta", [{"releases": "x"}, {"releases": [{"period": 2023}]},
                                  {"releases": [{**releases()[0], "lastReleased": "x"}]}])
def test_bad_release_meta_is_rejected(meta):
    item = sample(842, 156, 2023)
    with pytest.raises(ParseError):
        UnComtradeParser().parse(item.snapshot, item.data, meta)
