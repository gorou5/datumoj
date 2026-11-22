"""IMF WEO 2009-2010 archive files (tab-separated, cp1252) through ImfSdmxParser, against tests/fixtures/imf_weo_archive.

Only the planned import is produced: Greece GGXCNL_NGDP and its old code GGB_NGDP (owner's decision 2026-11-20).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.parsers.imf_sdmx import ImfSdmxParser
from datumoj.rikolto.testing.samples import make_snapshot

FIXTURES = Path(__file__).parents[2] / "tests/fixtures/imf_weo_archive"
SOURCE = "intl-imf-weo"
URL = "https://api.imf.org/external/sdmx/2.1/data/IMF.RES,WEO/GRC.GGXCNL_NGDP.A?startPeriod=2000&endPeriod=2015"
META = {"dataflow": "WEO", "key": "GRC.GGXCNL_NGDP.A"}
FILES = {v: FIXTURES / f"weo_{v.replace('-', '_').lower()}_GRC_excerpt.tsv"
         for v in ("2009-apr", "2009-oct", "2010-apr", "2010-oct")}
MIXED = {v: FIXTURES / f"weo_{v.replace('-', '_')}_mixed_excerpt.tsv" for v in ("2009-apr", "2010-oct")}


HEADER = (b"WEO Country Code\tISO\tWEO Subject Code\tCountry\tSubject Descriptor\tSubject Notes\tUnits\tScale\t"
          b"Country/Series-specific Notes\t2008\t2009\tEstimates Start After\r\n")


def row(code=b"GGXCNL_NGDP", scale=b"", v08=b"1.0", v09=b"2.0", est=b"2008", iso=b"GRC"):
    return b"\t".join([b"174", iso, code, b"G", b"S", b"N", b"Percent of GDP", scale, b"", v08, v09, est]) + b"\r\n"


def parse(version="2010-apr", data=None, meta=META):
    data = FILES[version].read_bytes() if data is None else data
    return ImfSdmxParser().parse(make_snapshot(SOURCE, URL, data), data, meta)


def one(result, area, indicator, year):
    found = [o for o in result.observations
             if (o.area_code, o.indicator_code, o.time_label) == (area, indicator, str(year))]
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize(("version", "code", "value", "status"), [
    ("2009-apr", "GGB_NGDP", "-4.509", "预测"),
    ("2009-oct", "GGB_NGDP", "-6.398", "预测"),
    ("2010-apr", "GGXCNL_NGDP", "-12.866", "实绩"),
    ("2010-oct", "GGXCNL_NGDP", "-13.584", "实绩"),
])
def test_greece_2009_per_version(version, code, value, status):
    obs = one(parse(version), "GRC", code, 2009)
    assert obs.value == Decimal(value) and obs.unit == "PT"
    assert obs.table_key == f"WEO:GRC.{code}.A" and obs.dims["COUNTRY"] == "GRC"
    assert obs.note.startswith(status) and "Scale=（空）" in obs.note
    assert obs.release_id is None and obs.released_at is None  # only the import command sets them


@pytest.mark.parametrize("version", FILES)
def test_only_the_planned_series_is_produced(version):
    result = parse(version)
    assert {(o.area_code, o.indicator_code) for o in result.observations} <= {
        ("GRC", "GGXCNL_NGDP"), ("GRC", "GGB_NGDP")}
    assert len(result.datasets) == 1
    assert "April" in str(result.datasets[0].extras["attribution"]) or "October" in str(result.datasets[0].extras["attribution"])


def test_old_codes_are_kept_and_mapped_in_the_note():
    obs = one(parse("2009-oct"), "GRC", "GGB_NGDP", 2009)
    assert obs.indicator_code == "GGB_NGDP" and obs.table_key == "WEO:GRC.GGB_NGDP.A"
    assert "GGB_NGDP → GGXCNL_NGDP" in obs.note and "GFSM 2001" in obs.note


API_NAME = "Net lending (+) / net borrowing (-), General government, Percent of GDP"  # SERIES_NAME in the API sample


@pytest.mark.parametrize("version", ["2010-apr", "2010-oct"])
def test_new_code_uses_api_indicator_name_and_keeps_subject_descriptor(version):
    for result in (parse(version), parse(data=MIXED[version].read_bytes()) if version in MIXED else parse(version)):
        obs = one(result, "GRC", "GGXCNL_NGDP", 2009)
        assert obs.indicator == API_NAME
        assert "Subject Descriptor=General government net lending/borrowing" in obs.note
        assert obs.table_key == "WEO:GRC.GGXCNL_NGDP.A"
        assert obs.dims == {"COUNTRY": "GRC", "INDICATOR": "GGXCNL_NGDP", "FREQUENCY": "A", "TIME_PERIOD": "2009"}


def test_api_sample_series_name_matches_the_constant():
    api = Path(__file__).parents[2] / "tests/fixtures/imf_weo/weo_GRC_GGXCNL_NGDP_2000-2015.csv"
    assert API_NAME in api.read_text(encoding="utf-8")


@pytest.mark.parametrize("version", ["2009-apr", "2009-oct"])
def test_old_code_keeps_original_name_and_is_not_merged(version):
    obs = one(parse(version), "GRC", "GGB_NGDP", 2009)
    assert obs.indicator == "General government balance"
    assert obs.indicator_code == "GGB_NGDP" and obs.dims["INDICATOR"] == "GGB_NGDP"
    assert "Subject Descriptor=" not in obs.note


def test_new_codes_carry_no_mapping():
    assert "代码对应" not in one(parse("2010-apr"), "GRC", "GGXCNL_NGDP", 2009).note


def test_local_currency_series_are_not_produced():
    for version in FILES:
        assert not [o for o in parse(version).observations if o.indicator_code in {"GGXCNL", "GGB", "NGDPD"}]


def test_scale_is_applied_to_planned_rows():
    # The scale logic stays: planned rows built here (the real planned series are percentages, scale empty).
    for scale, factor in ((b"Millions", 10**6), (b"Billions", 10**9), (b"Units", 1), (b"", 1)):
        result = parse(data=HEADER + row(scale=scale))
        assert one(result, "GRC", "GGXCNL_NGDP", 2008).value == Decimal(1) * factor
    for scale in (b"Thousands", b"billions"):
        with pytest.raises(ParseError, match="Scale"):
            parse(data=HEADER + row(scale=scale))


@pytest.mark.parametrize("version", MIXED)
def test_mixed_samples_only_produce_the_planned_greek_row(version):
    result = parse(data=MIXED[version].read_bytes())
    assert {(o.area_code, o.table_key) for o in result.observations} == {
        ("GRC", "WEO:GRC.GGB_NGDP.A" if version == "2009-apr" else "WEO:GRC.GGXCNL_NGDP.A")}
    assert not {o.area_code for o in result.observations} & {"JPN", "AGO", "BTN", "ALB", "BDI"}
    assert one(result, "GRC", "GGB_NGDP" if version == "2009-apr" else "GGXCNL_NGDP", 2009).value == Decimal(
        "-4.509" if version == "2009-apr" else "-13.584")


def test_na_years_have_no_observation_and_nothing_is_zero():
    result = parse(data=MIXED["2009-apr"].read_bytes())
    assert not [o for o in result.observations if o.time_label in {"2011", "2012", "2013", "2014"}]
    assert not [o for o in result.observations if o.value == 0]
    dashes = parse(data=HEADER + row(v08=b"--", v09=b"3"))
    assert [o.time_label for o in dashes.observations] == ["2009"]
    with pytest.raises(ParseError):  # nothing but annotations: no data rows
        parse(data=HEADER + row(v08=b"--", v09=b"n/a"))


def test_footer_and_blank_lines_are_not_data():
    result = parse(data=MIXED["2010-oct"].read_bytes())  # all-tab line and the quoted source line
    assert len(result.datasets) == 1 and "October 2010" in str(result.datasets[0].extras["attribution"])


def test_estimates_start_after_zero_or_empty_is_unknown():
    result = parse("2009-apr")  # Estimates Start After = 2007
    assert one(result, "GRC", "GGB_NGDP", 2007).note.startswith("实绩")
    assert one(result, "GRC", "GGB_NGDP", 2008).note.startswith("预测")
    for est in (b"0", b""):
        unknown = parse(data=HEADER + row(est=est))
        assert one(unknown, "GRC", "GGXCNL_NGDP", 2009).note.startswith("实绩/预测未知")
    with pytest.raises(ParseError, match="Estimates Start After"):
        parse(data=HEADER + row(est=b"abc"))


def test_unknown_value_and_year_in_planned_rows_are_errors_but_not_elsewhere():
    with pytest.raises(ParseError, match="数值"):
        parse(data=HEADER + row(v09=b"x"))
    parse(data=HEADER + row(v09=b"1") + row(code=b"NGDP", scale=b"Weird", v09=b"x", iso=b"JPN"))  # unplanned: skipped
    with pytest.raises(ParseError):  # planned row with an unknown scale, edited in the bytes
        parse(data=MIXED["2010-oct"].read_bytes().replace(b"\tPercent of GDP\t\t", b"\tPercent of GDP\tWeird\t"))


def test_same_observation_has_one_identity_across_versions():
    assert one(parse("2010-apr"), "GRC", "GGXCNL_NGDP", 2009).obs_id == one(parse("2010-oct"), "GRC", "GGXCNL_NGDP", 2009).obs_id


@pytest.mark.parametrize("meta", [
    {"dataflow": "WEO_2026_APR_VINTAGE", "key": "GRC.GGXCNL_NGDP.A"},
    {"dataflow": "WEO_2025_OCT_VINTAGE", "key": "GRC.GGXCNL_NGDP.A"},
    {"dataflow": "WEO", "key": "JPN+USA+DEU+FRA+GBR.NGDPD.A"},
    {"dataflow": "WEO", "key": "JPN+USA+DEU+FRA+GBR.NGDP.A"},
    {"dataflow": "WEO", "key": "GRC.GGXCNL.A"},
    {"dataflow": "WEO"},
    None,
])
def test_only_the_planned_request_claims_the_file(meta):
    parse(meta=META)
    with pytest.raises(ParseError):
        parse(meta=meta)


@pytest.mark.parametrize("data", [
    b"Country\tISO\r\nx\ty\r\n",
    b"",
    HEADER,
    HEADER + b"1\tGRC\tX\tG\tS\tN\tU\t\t\t1.0\t2.0\t\r\n",
    HEADER + row(iso=b"JPN"),  # nothing planned in the file
])
def test_unrecognised_or_broken_files_are_errors(data):
    with pytest.raises(ParseError):
        parse(data=data)


@pytest.mark.parametrize("scale", [b"Millions", b"Billions", b"Units", b""])
def test_dataset_declares_scale_one_because_values_were_converted(scale):
    # 317: the Scale column was applied to the values, so the dataset's values are full values.
    declared = parse(data=HEADER + row(scale=scale)).datasets[0].extras["scale"]
    assert declared["multiplier"] == 1.0
    assert f"Scale={scale.decode() or '（空）'}" in declared["evidence"]


@pytest.mark.parametrize("version", FILES)
def test_recorded_archive_datasets_carry_the_scale_declaration(version):
    assert all(d.extras["scale"]["multiplier"] == 1.0 for d in parse(version).datasets)
