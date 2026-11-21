"""OECD SDMX-CSV parser on the recorded samples (tests/fixtures/oecd_sdmx).

The recorded files are used untouched, except where a helper says "synthetic": the per-EDITION split of the
revisions sample keeps whole recorded lines; the other dataflows' contentconstraints are the recorded
NAMAIN10 one with ids/versions/validFrom rewritten.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError, discover_parsers
from datumoj.rikolto.core.models import TimeGranularity
from datumoj.rikolto.parsers.oecd_sdmx import OecdSdmxParser, _period, read_code_labels, read_valid_from
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/oecd_sdmx"
BASE = "https://sdmx.oecd.org/public/rest/"
NAMAIN, HOU, STES = "DSD_NAMAIN10@DF_TABLE1_EXPENDITURE", "DSD_EAR@DF_HOU_EAR", "DSD_STES_REVISIONS@DF_STES_REVISIONS"
AGENCY = {NAMAIN: "OECD.SDD.NAD", HOU: "OECD.SDD.TPS", STES: "OECD.SDD.STES"}
VERSION = {NAMAIN: "2.0", HOU: "1.0", STES: "4.0"}
RECORDED_VALID_FROM = "2026-11-16T07:54:30Z"
NAMAIN_URL = (BASE + f"data/{AGENCY[NAMAIN]},{NAMAIN},2.0/A.JPN+USA.S1.S1.B1GQ._Z._Z._Z.XDC+USD_EXC.V.N.T0102"
              "?startPeriod=2020&endPeriod=2024&format=csv")
HOU_URL = BASE + f"data/{AGENCY[HOU]},{HOU},1.0/JPN.EAR.IX.N.C.S1.M?startPeriod=2018-01&endPeriod=2018-12&format=csv"


def edition_url(edition):
    return (BASE + f"data/{AGENCY[STES]},{STES},4.0/JPN.M.H_EARN.IX.C.{edition}"
            "?startPeriod=2017-10&endPeriod=2018-03&format=csv")


def constraint_url(flow):
    return BASE + f"contentconstraint/{AGENCY[flow]}/CR_A_{flow}/"


def structure_url(flow):
    return BASE + f"dataflow/{AGENCY[flow]}/{flow}/{VERSION[flow]}?references=all&detail=referencepartial"


def structure_bytes():
    """The recorded NAMAIN10 structure with codelists (derived: only codes of the data sample are kept).

    Crawler/e2e tests serve it for the other dataflows too; it is only a codelist source there.
    """
    return (FIXTURES / "dataflow_DF_TABLE1_EXPENDITURE_codelists.xml").read_bytes()


def namain_bytes():
    return (FIXTURES / "namain10_JPN-USA_2020-2024.csv").read_bytes()


def hou_bytes():
    return (FIXTURES / "hou_ear_JPN_2018.csv").read_bytes()


def edition_bytes(edition):
    """Synthetic split of the recorded revisions sample: whole recorded lines of one EDITION (column 7)."""
    lines = (FIXTURES / "stes_revisions_JPN_201901-201902.csv").read_text().splitlines(keepends=True)
    return "".join(ln for i, ln in enumerate(lines) if i == 0 or ln.split(",")[6] == edition).encode()


def constraint_bytes(flow=NAMAIN, valid_from=RECORDED_VALID_FROM):
    """The recorded NAMAIN10 contentconstraint; other flows / validFrom are synthetic rewrites of it."""
    text = (FIXTURES / "contentconstraint_CR_A_DF_TABLE1_EXPENDITURE.xml").read_text()
    text = text.replace(RECORDED_VALID_FROM, valid_from)
    if flow != NAMAIN:
        text = text.replace(NAMAIN, flow).replace(AGENCY[NAMAIN], AGENCY[flow]).replace('version="2.0"', f'version="{VERSION[flow]}"')
    return text.encode()


def meta_for(flow, **extra):
    return {"agency": AGENCY[flow], "id": flow, "version": VERSION[flow], **extra}


def sample(flow, data, url, **extra):
    return ParseSample(make_snapshot("intl-oecd", url, data), data, meta_for(flow, **extra))


def parse(item):
    return OecdSdmxParser().parse(item.snapshot, item.data, item.meta)


class TestOecdParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return OecdSdmxParser()

    @pytest.fixture
    def samples(self):
        return [
            sample(NAMAIN, namain_bytes(), NAMAIN_URL, valid_from=RECORDED_VALID_FROM),
            sample(HOU, hou_bytes(), HOU_URL, valid_from="2026-11-01T00:00:00Z"),
            sample(STES, (FIXTURES / "stes_revisions_JPN_201901-201902.csv").read_bytes(), edition_url("201901")),
        ]

    @pytest.fixture
    def bad_samples(self):
        header = b"DATAFLOW,REF_AREA,TIME_PERIOD,OBS_VALUE\n"
        return [
            b"not csv", b"<html/>", b'<!DOCTYPE x [<!ENTITY e "unsafe">]><x>&e;</x>',
            b"DATAFLOW,REF_AREA,TIME_PERIOD\nA,JPN,2020\n",  # no OBS_VALUE
            header + b"OECD.SDD.NAD:DSD_X@DF_X(1.0),JPN,2020,12abc\n",  # not a number
            header + b"OECD.SDD.NAD:DSD_X@DF_X(1.0),JPN,2020,NaN\n",
            header + b"OECD.SDD.NAD:DSD_X@DF_X(1.0),JPN,2020\n",  # short row
            header + b"not-a-flow,JPN,2020,1\n",
        ]


def test_plugin_is_discovered():
    assert discover_parsers()[("oecd_sdmx", "*")] is OecdSdmxParser


def test_nominal_gdp_keeps_values_unit_mult_and_currency_apart():
    parsed = parse(sample(NAMAIN, namain_bytes(), NAMAIN_URL, valid_from=RECORDED_VALID_FROM))
    assert len(parsed.observations) == 20
    by_key = {(o.area_code, o.dims["UNIT_MEASURE"], o.time_label): o for o in parsed.observations}
    assert len(by_key) == 20  # XDC and USD_EXC never collapse into one observation
    jpn = by_key["JPN", "XDC", "2020"]
    assert jpn.value == Decimal(554074400) and str(jpn.value) == "554074400"  # verbatim, not scaled
    assert jpn.unit == "JPY ×10^6" and "UNIT_MULT=6" in jpn.note and "CURRENCY=JPY" in jpn.note
    assert by_key["USA", "XDC", "2020"].unit == "USD ×10^6"
    exc = by_key["JPN", "USD_EXC", "2020"]
    assert str(exc.value) == "5189197.544" and exc.unit == "USD_EXC ×10^6"
    assert exc.obs_id != jpn.obs_id
    assert jpn.dims == {
        "FREQ": "A", "REF_AREA": "JPN", "SECTOR": "S1", "COUNTERPART_SECTOR": "S1", "TRANSACTION": "B1GQ",
        "INSTR_ASSET": "_Z", "ACTIVITY": "_Z", "EXPENDITURE": "_Z", "UNIT_MEASURE": "XDC", "PRICE_BASE": "V",
        "TRANSFORMATION": "N", "TABLE_IDENTIFIER": "T0102", "TIME_PERIOD": "2020"}
    assert (jpn.indicator_code, jpn.table_key, jpn.dataset_key) == ("B1GQ", "df_table1_expenditure", "intl-oecd:df_table1_expenditure")
    assert (jpn.time_start, jpn.time_end, jpn.granularity) == (date(2020, 1, 1), date(2020, 12, 31), TimeGranularity.year)
    assert {o.release_id for o in parsed.observations} == {f"{NAMAIN}@{RECORDED_VALID_FROM}"}
    assert {o.released_at for o in parsed.observations} == {date(2026, 11, 16)}
    assert parsed.datasets[0].extras["dataflow"] == NAMAIN


def test_multiplier_comes_from_the_unit_mult_column_not_a_constant():
    text = namain_bytes().decode().replace(",F,1,A,6,JPY", ",F,1,A,3,JPY")
    parsed = parse(sample(NAMAIN, text.encode(), NAMAIN_URL, valid_from=RECORDED_VALID_FROM))
    jpn = next(o for o in parsed.observations if o.area_code == "JPN" and o.dims["UNIT_MEASURE"] == "XDC")
    assert jpn.unit == "JPY ×10^3" and "UNIT_MULT=3" in jpn.note and str(jpn.value) == "554074400"
    with pytest.raises(ParseError, match="UNIT_MULT"):
        parse(sample(NAMAIN, text.replace("A,3,JPY", "A,x,JPY", 1).encode(), NAMAIN_URL))


def test_no_valid_from_means_no_release_identity():
    parsed = parse(sample(NAMAIN, namain_bytes(), NAMAIN_URL))
    assert all(o.release_id is None and o.released_at is None for o in parsed.observations)


def test_revision_editions_are_releases_of_the_same_observation():
    data = (FIXTURES / "stes_revisions_JPN_201901-201902.csv").read_bytes()
    parsed = parse(sample(STES, data, edition_url("201901")))
    assert len(parsed.observations) == 12
    rows = [o for o in parsed.observations if o.time_label == "2018-01"]
    by_edition = {o.release_id: o for o in rows}
    assert {k: str(o.value) for k, o in by_edition.items()} == {"201901": "81.41", "201902": "81.51"}
    assert {k: o.released_at for k, o in by_edition.items()} == {"201901": date(2019, 1, 1), "201902": date(2019, 2, 1)}
    assert len({o.obs_id for o in rows}) == 1  # same logical observation, two releases
    assert by_edition["201901"].dims["EDITION"] == "201901" and by_edition["201901"].unit == "IX"
    assert (by_edition["201901"].time_start, by_edition["201901"].granularity) == (date(2018, 1, 1), TimeGranularity.month)
    assert "BASE_PER=2015" in by_edition["201901"].note
    # One file per EDITION (what the crawler requests) gives the same values.
    for edition, expected in (("201901", "81.41"), ("201902", "81.51")):
        one = parse(sample(STES, edition_bytes(edition), edition_url(edition)))
        assert {o.release_id for o in one.observations} == {edition}
        assert [str(o.value) for o in one.observations if o.time_label == "2018-01"] == [expected]


def test_current_hourly_earnings_value_and_base_period():
    parsed = parse(sample(HOU, hou_bytes(), HOU_URL))
    assert len(parsed.observations) == 12
    jan = next(o for o in parsed.observations if o.time_label == "2018-01")
    assert (str(jan.value), jan.unit, jan.indicator_code, jan.area_code) == ("81.56017", "IX", "EAR", "JPN")
    assert "BASE_PER=2015" in jan.note and "UNIT_MULT=0" in jan.note


def test_missing_values_are_empty_with_the_note_never_zero():
    line = "OECD.SDD.TPS:DSD_EAR@DF_HOU_EAR(1.0),JPN,EAR,IX,N,C,S1,M,2018-01,81.56017,2015,A,0,2"
    text = hou_bytes().decode()
    assert line in text
    for raw, status, expected in (("", "M", "OBS_VALUE=missing"), ("-", "M", "OBS_VALUE=-"), ("…", "L", "OBS_VALUE=…")):
        changed = text.replace(line, line.replace("81.56017", raw).replace(",A,", f",{status},"))
        jan = next(o for o in parse(sample(HOU, changed.encode(), HOU_URL)).observations if o.time_label == "2018-01")
        assert jan.value is None and expected in jan.note and f"OBS_STATUS={status}" in jan.note


@pytest.mark.parametrize("raw", ["12abc", "NaN", "Infinity"])
def test_non_numeric_value_is_an_error_not_a_zero(raw):
    text = hou_bytes().decode().replace("81.56017", raw)
    with pytest.raises(ParseError, match="OBS_VALUE"):
        parse(sample(HOU, text.encode(), HOU_URL))


@pytest.mark.parametrize("changes", [{"version": "3.0"}, {"id": HOU}, {"agency": "OECD.SDD.XXX"}])
def test_dataflow_that_differs_from_the_plan_is_an_error(changes):
    item = sample(NAMAIN, namain_bytes(), NAMAIN_URL)
    with pytest.raises(ParseError, match="数据流"):
        OecdSdmxParser().parse(item.snapshot, item.data, {**item.meta, **changes})


def test_bad_edition_is_an_error():
    text = edition_bytes("201901").decode().replace(",201901,", ",2019-1,")
    with pytest.raises(ParseError, match="EDITION"):
        parse(sample(STES, text.encode(), edition_url("201901")))


def test_period_labels():
    assert _period("2018-Q2") == (date(2018, 4, 1), date(2018, 6, 30), TimeGranularity.quarter)
    assert _period("2018-S2") == (date(2018, 7, 1), date(2018, 12, 31), TimeGranularity.half_year)
    assert _period("2018-02") == (date(2018, 2, 1), date(2018, 2, 28), TimeGranularity.month)
    assert _period("2018-02-03") == (date(2018, 2, 3), date(2018, 2, 3), TimeGranularity.day)
    assert _period("2018-B") == (None, None, TimeGranularity.other)
    with pytest.raises(ParseError):
        _period("2018-13")


def test_contentconstraint_valid_from_and_its_snapshot_parses_to_nothing():
    data = constraint_bytes()
    assert read_valid_from(data, AGENCY[NAMAIN], NAMAIN) == RECORDED_VALID_FROM
    assert read_valid_from(constraint_bytes(HOU, "2026-11-01T00:00:00Z"), AGENCY[HOU], HOU) == "2026-11-01T00:00:00Z"
    for agency, flow in ((AGENCY[HOU], NAMAIN), (AGENCY[NAMAIN], HOU)):
        with pytest.raises(ParseError):
            read_valid_from(data, agency, flow)
    parsed = parse(sample(NAMAIN, data, constraint_url(NAMAIN)))
    assert not (parsed.observations or parsed.datasets or parsed.documents)
    with pytest.raises(ParseError):
        read_valid_from(data.replace(b'validFrom="2026-11-16T07:54:30Z"', b'validFrom="soon"'), AGENCY[NAMAIN], NAMAIN)
    with pytest.raises(ParseError):
        read_valid_from(data.replace(b' validFrom="2026-11-16T07:54:30Z"', b""), AGENCY[NAMAIN], NAMAIN)


def test_code_labels_come_from_the_structure_codelists_for_used_codes_only():
    labels = read_code_labels(structure_bytes())
    assert labels["TRANSACTION"]["B1GQ"] == "Gross domestic product"
    plain = parse(sample(NAMAIN, namain_bytes(), NAMAIN_URL))
    extra = dict(labels, TRANSACTION=dict(labels["TRANSACTION"], XXX="unused"))
    with_labels = parse(sample(NAMAIN, namain_bytes(), NAMAIN_URL, code_labels=extra))
    assert plain.datasets[0].extras["code_labels"] == {}  # no structure: nothing invented
    assert with_labels.datasets[0].extras["code_labels"] == {"TRANSACTION": {"B1GQ": "Gross domestic product"}}
    # Observation fields, and with them the series identity, do not depend on the codelists.
    assert [o.model_dump() for o in plain.observations] == [o.model_dump() for o in with_labels.observations]
    assert {(o.indicator, o.indicator_code) for o in with_labels.observations} == {("B1GQ", "B1GQ")}


def test_structure_snapshot_parses_to_nothing():
    parsed = parse(sample(NAMAIN, structure_bytes(), structure_url(NAMAIN)))
    assert not (parsed.observations or parsed.datasets or parsed.documents)
