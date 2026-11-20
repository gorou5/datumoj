"""IMF WEO 2009-2010 archive files (tab-separated, cp1252) through ImfSdmxParser, against tests/fixtures/imf_weo_archive."""

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
FILES = {v: FIXTURES / f"weo_{v.replace('-', '_').lower()}_GRC_excerpt.tsv"
         for v in ("2009-apr", "2009-oct", "2010-apr", "2010-oct")}


HEADER = (b"WEO Country Code\tISO\tWEO Subject Code\tCountry\tSubject Descriptor\tSubject Notes\tUnits\tScale\t"
          b"Country/Series-specific Notes\t2009\tEstimates Start After\r\n")


def parse(version="2010-apr", data=None, meta=None):
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


@pytest.mark.parametrize(("version", "code"), [("2009-apr", "GGB_NGDP"), ("2009-oct", "GGB")])
def test_old_codes_are_kept_and_mapped_in_the_note(version, code):
    obs = one(parse(version), "GRC", code, 2009)
    assert obs.indicator_code == code and obs.table_key == f"WEO:GRC.{code}.A"
    assert f"{code} → {code.replace('GGB', 'GGXCNL')}" in obs.note and "GFSM 2001" in obs.note


def test_new_codes_carry_no_mapping():
    assert "代码对应" not in one(parse("2010-apr"), "GRC", "GGXCNL_NGDP", 2009).note


def test_local_currency_is_scaled_to_the_full_value_like_sdmx():
    gap = one(parse("2010-apr"), "GRC", "GGXCNL", 2009)
    assert gap.value == Decimal(-30557000000) and gap.unit == "XDC" and "Scale=Billions" in gap.note
    assert one(parse("2009-oct"), "GRC", "GGB", 2009).value == Decimal(-15757000000)
    assert one(parse("2010-apr"), "CIV", "NGDPD", 2009).value == Decimal(22497000000)  # Billions of USD


def test_millions_are_scaled_and_unknown_scale_is_an_error():
    data = FILES["2010-apr"].read_bytes()
    assert one(parse(data=data.replace(b"\tBillions\t", b"\tMillions\t")), "GRC", "GGXCNL", 2009).value == Decimal(-30557000)
    for scale in (b"Thousands", b"billions"):
        with pytest.raises(ParseError, match="Scale"):
            parse(data=data.replace(b"\tBillions\t", b"\t" + scale + b"\t"))


def test_country_comes_from_iso_and_name_is_cp1252():
    result = parse("2010-apr")
    assert {o.area_code for o in result.observations} == {"CIV", "GRC"}  # not 662 / 174
    assert one(result, "CIV", "NGDPD", 2009).value == Decimal(22497000000)
    assert "Côte d'Ivoire" in FILES["2010-apr"].read_bytes().decode("cp1252")


def test_forecast_actual_split_follows_estimates_start_after():
    result = parse("2009-apr")  # Estimates Start After = 2007
    assert one(result, "GRC", "GGB_NGDP", 2007).note.startswith("实绩")
    assert one(result, "GRC", "GGB_NGDP", 2008).note.startswith("预测")
    data = FILES["2009-apr"].read_bytes()
    unknown = parse(data=data.replace(b"\t2007\r\n", b"\t\r\n"))
    assert one(unknown, "GRC", "GGB_NGDP", 2008).note.startswith("实绩/预测未知")


def test_empty_cells_skipped_and_annotations_never_become_zero():
    data = FILES["2010-apr"].read_bytes()
    result = parse(data=data)
    assert not [o for o in result.observations if o.value == 0]
    na = one(result, "GRC", "GGXCNL", 2012)
    assert na.value is None and "OBS_VALUE=n/a" in na.note
    blank = data.replace(b"\t-30.557\t", b"\t\t")
    assert not [o for o in parse(data=blank).observations if (o.indicator_code, o.time_label) == ("GGXCNL", "2009")]


@pytest.mark.parametrize("version", FILES)
def test_footer_and_blank_lines_are_not_data(version):
    result = parse(version)
    assert {o.area_code for o in result.observations} == {"CIV", "GRC"}
    assert "April" in str(result.datasets[0].extras["attribution"]) or "October" in str(result.datasets[0].extras["attribution"])


def test_same_observation_has_one_identity_across_versions():
    assert one(parse("2010-apr"), "GRC", "GGXCNL_NGDP", 2009).obs_id == one(parse("2010-oct"), "GRC", "GGXCNL_NGDP", 2009).obs_id


def test_vintage_requests_do_not_claim_the_file():
    parse(meta={"dataflow": "WEO"})
    with pytest.raises(ParseError):
        parse(meta={"dataflow": "WEO_2026_APR_VINTAGE"})


@pytest.mark.parametrize("data", [
    b"Country\tISO\r\nx\ty\r\n",
    b"",
    HEADER,
    HEADER + b"1\tGRC\tX\tG\tS\tN\tU\t\t\t1.0\r\n",
])
def test_unrecognised_or_broken_files_are_errors(data):
    with pytest.raises(ParseError):
        parse(data=data)
