"""IMF WEO SDMX-CSV parser, against the recorded samples in tests/fixtures/imf_weo."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError, discover_parsers
from datumoj.rikolto.core.models import TimeGranularity
from datumoj.rikolto.parsers.imf_sdmx import ImfSdmxParser, parse_key, release_of
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/imf_weo"
BASE = "https://api.imf.org/external/sdmx/2.1/"
SOURCE = "intl-imf-weo"
GRC_KEY = "GRC.GGXCNL_NGDP.A"
GDP_USD_KEY = "JPN+USA.NGDPD.A"
FILES = {
    "current": ("WEO", GRC_KEY, "2000", "2015", "weo_GRC_GGXCNL_NGDP_2000-2015.csv"),
    "apr": ("WEO_2026_APR_VINTAGE", GRC_KEY, "2000", "2015", "weo_2026_apr_GRC_GGXCNL_NGDP_2000-2015.csv"),
    "oct": ("WEO_2025_OCT_VINTAGE", GRC_KEY, "2000", "2015", "weo_2025_oct_GRC_GGXCNL_NGDP_2000-2015.csv"),
    "usd": ("WEO", GDP_USD_KEY, "2020", "2030", "weo_JPN-USA_NGDPD_2020-2030.csv"),
    "lcu": ("WEO", "JPN.NGDP.A", "2020", "2024", "weo_JPN_NGDP_2020-2024.csv"),
}


def meta_of(name):
    flow, key, start, end, _ = FILES[name]
    return {"dataflow": flow, "key": key, "startPeriod": start, "endPeriod": end}


def data_of(name):
    return (FIXTURES / FILES[name][4]).read_bytes()


def sample(name="current", data=None, meta=None):
    data = data_of(name) if data is None else data
    flow, key, start, end, _ = FILES[name]
    url = f"{BASE}data/IMF.RES,{flow}/{key}?startPeriod={start}&endPeriod={end}"
    return ParseSample(make_snapshot(SOURCE, url, data), data, meta if meta is not None else meta_of(name))


def parse(name="current", data=None, meta=None):
    s = sample(name, data, meta)
    return ImfSdmxParser().parse(s.snapshot, s.data, s.meta)


def by(result, area, year, indicator=None):
    found = [o for o in result.observations if o.area_code == area and o.time_label == str(year)
             and indicator in (None, o.indicator_code)]
    assert len(found) == 1
    return found[0]


def csv_rows(name):
    return list(csv.DictReader(io.StringIO(data_of(name).decode())))


class TestImfSdmxParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return ImfSdmxParser()

    @pytest.fixture
    def samples(self):
        return [sample(name) for name in FILES]


def test_plugin_is_discovered():
    assert discover_parsers()[("imf_sdmx", "*")] is ImfSdmxParser


def test_greece_2009_net_lending_as_percent_of_gdp():
    result = parse()
    obs = by(result, "GRC", 2009)
    assert obs.value == Decimal("-15.423608")
    assert obs.unit == "PT"
    assert (obs.indicator_code, obs.area_code, obs.time_label) == ("GGXCNL_NGDP", "GRC", "2009")
    assert obs.table_key == f"WEO:{GRC_KEY}" and obs.dataset_key == f"{SOURCE}:WEO:{GRC_KEY}"
    assert obs.granularity is TimeGranularity.year and obs.time_start == date(2009, 1, 1) and obs.time_end == date(2009, 12, 31)
    assert obs.dims == {"COUNTRY": "GRC", "INDICATOR": "GGXCNL_NGDP", "FREQUENCY": "A", "TIME_PERIOD": "2009"}
    assert obs.indicator.startswith("Net lending (+) / net borrowing (-)")
    assert len(result.observations) == 16
    assert {o.locator for o in result.observations} == {f"row={n}" for n in range(1, 17)}
    assert result.datasets[0].dataset_key == obs.dataset_key  # a dataset per table: no overwriting between series


@pytest.mark.parametrize("name", ["usd", "lcu", "current", "apr", "oct"])
def test_values_are_kept_exactly_as_published_and_never_scaled(name):
    rows = csv_rows(name)
    result = parse(name)
    assert len(result.observations) == len(rows)
    for row, obs in zip(rows, result.observations, strict=True):
        assert obs.value is not None
        assert str(obs.value) == row["OBS_VALUE"]  # verbatim: no SCALE multiplication, no rounding
        assert obs.value == Decimal(row["OBS_VALUE"])
    if name == "usd":
        # SCALE=9 is only the display magnitude: the value is already in US dollars
        assert {r["SCALE"] for r in rows} == {"9"}
        assert by(result, "JPN", 2024).value == Decimal(4207630342000)
        assert by(result, "USA", 2024).value == Decimal(29381025000000)
        assert {o.unit for o in result.observations} == {"USD"}


def test_local_currency_series_records_the_currency():
    result = parse("lcu")
    obs = by(result, "JPN", 2024)
    assert obs.unit == "XDC" and obs.value == Decimal(636893400000000)
    currency = csv_rows("lcu")[0]["PRIMARY_DOMESTIC_CURRENCY"]
    assert currency and f"PRIMARY_DOMESTIC_CURRENCY={currency}" in obs.note
    # only local-currency series carry it
    assert "PRIMARY_DOMESTIC_CURRENCY" not in by(parse(), "GRC", 2009).note


def test_forecast_years_are_marked_and_actuals_are_not():
    rows = csv_rows("usd")
    result = parse("usd")
    latest = {r["LATEST_ACTUAL_ANNUAL_DATA"] for r in rows}
    assert latest == {"2025"}
    for obs in result.observations:
        expected = "预测" if int(obs.time_label) > 2025 else "实绩"
        assert obs.note.split("; ")[0] == expected, (obs.area_code, obs.time_label, obs.note)
    assert by(result, "JPN", 2025).note.startswith("实绩") and by(result, "JPN", 2026).note.startswith("预测")
    assert by(result, "USA", 2030).note.startswith("预测")
    # the October 2025 vintage had 2024 as its last actual year
    assert {csv_rows("oct")[0]["LATEST_ACTUAL_ANNUAL_DATA"]} == {"2024"}
    assert all(o.note.startswith("实绩") for o in parse("oct").observations)  # 2000-2015 are all actuals


def test_unknown_actual_year_is_never_labelled_actual():
    data = data_of("current").decode()
    assert data.count(",2025,National Statistics Office,") == 16
    obs = by(parse(data=data.replace(",2025,National Statistics Office,", ",,National Statistics Office,").encode()), "GRC", 2009)
    assert obs.note.startswith("实绩/预测未知") and "实绩;" not in obs.note and not obs.note.startswith("预测")


def test_release_comes_from_the_publication_date_and_series_update_date_is_not_a_release():
    current, apr, oct_ = parse("current"), parse("apr"), parse("oct")
    expected = {"current": ("WEO-2026-OCT", date(2026, 10, 13)), "apr": ("WEO-2026-APR", date(2026, 4, 14)),
                "oct": ("WEO-2025-OCT", date(2025, 10, 14))}
    for name, result in (("current", current), ("apr", apr), ("oct", oct_)):
        assert {(o.release_id, o.released_at) for o in result.observations} == {expected[name]}
    # COUNTRY_UPDATE_DATE (9/25/2026) is kept as a note, not used as the release
    note = by(current, "GRC", 2009).note
    assert "COUNTRY_UPDATE_DATE=9/25/2026" in note and "dataflow=IMF.RES:WEO(9.0.0)" in note
    assert "dataflow=IMF.RES:WEO_2026_APR_VINTAGE(1.0.0)" in by(apr, "GRC", 2009).note


def test_same_observation_in_two_releases_has_one_identity_and_two_releases():
    current, apr, oct_ = (by(parse(n), "GRC", 2009) for n in ("current", "apr", "oct"))
    assert current.obs_id == apr.obs_id == oct_.obs_id and current.table_key == apr.table_key == oct_.table_key
    assert len({current.release_id, apr.release_id, oct_.release_id}) == 3
    assert [o.value for o in (oct_, apr, current)] == [Decimal("-15.424"), Decimal("-15.423608"), Decimal("-15.423608")]
    assert len({current.snapshot_sha256, apr.snapshot_sha256, oct_.snapshot_sha256}) == 3
    # obs_id depends only on the observation's own coordinates
    assert by(parse("usd"), "JPN", 2024).obs_id != by(parse("usd"), "USA", 2024).obs_id


def test_empty_data_set_for_a_wrong_key_is_a_failure():
    data = (FIXTURES / "weo_bad_key.csv").read_bytes()
    assert len(list(csv.DictReader(io.StringIO(data.decode())))) == 1  # one attributes-only row, 200 OK
    meta = {"dataflow": "WEO", "key": "GRC.NO_SUCH_INDICATOR.A", "startPeriod": "2000", "endPeriod": "2015"}
    with pytest.raises(ParseError, match="缺少请求的序列"):
        ImfSdmxParser().parse(make_snapshot(SOURCE, BASE + "x", data), data, meta)


def test_header_only_and_missing_series_are_failures():
    header = data_of("current").decode().splitlines()[0] + "\n"
    with pytest.raises(ParseError, match="缺少请求的序列"):
        parse(data=header.encode())
    with pytest.raises(ParseError, match="缺少请求的序列"):
        parse("usd", meta={**meta_of("usd"), "key": "JPN+USA+DEU.NGDPD.A"})  # DEU was requested but not returned
    with pytest.raises(ParseError, match="缺少请求的序列"):
        parse("usd", meta={**meta_of("usd"), "key": "JPN+USA.NGDPD+NGDP.A"})


@pytest.mark.parametrize("change", [
    {"dataflow": "WEO_2026_APR_VINTAGE"},  # response is the current flow
    {"key": "JPN.NGDPD.A", "startPeriod": "2020", "endPeriod": "2030"},  # USA rows were not requested
    {"endPeriod": "2026"},  # 2027+ rows are outside the request
    {"key": "JPN+USA.NGDPD.Q"},
    {"dataflow": "WEO_2026_JUL_VINTAGE"},
    {"startPeriod": "20x0"},
    {"key": None},
    {"dataflow": None},
])
def test_response_must_match_the_request(change):
    with pytest.raises(ParseError):
        parse("usd", meta={**meta_of("usd"), **change})


def test_missing_request_context_fails():
    with pytest.raises(ParseError):
        parse(meta={})


@pytest.mark.parametrize("old,new", [
    (",GRC,GGXCNL_NGDP,A,2009,", ",GRC,GGXCNL_NGDP,A,,"),  # observation without a period
    (",GRC,GGXCNL_NGDP,A,2009,", ",GRC,GGXCNL_NGDP,A,Q1-2009,"),
    (",GRC,GGXCNL_NGDP,A,2009,", ",GRC,GGXCNL_NGDP,A,2008,"),  # duplicate 2008
    (",GRC,GGXCNL_NGDP,A,2009,", ",ITA,GGXCNL_NGDP,A,2009,"),
    ("IMF.RES:WEO(9.0.0),GRC,GGXCNL_NGDP,A,2009,", "IMF.RES:OTHER(1.0.0),GRC,GGXCNL_NGDP,A,2009,"),
    ("2026-10-13T02:00:00Z,2026-10-13T02:00:00Z", "2026-10-13T02:00:00Z,2026-10-13T02:00:00Z,extra"),  # row too long
])
def test_malformed_rows_are_failures(old, new):
    text = data_of("current").decode()
    assert text.count(old) >= 1
    with pytest.raises(ParseError):
        parse(data=text.replace(old, new, 1).encode())


def test_not_csv_or_wrong_encoding_is_a_failure():
    for data in (b"", b"<html>blocked</html>", "GRC".encode("cp932") + b"\x82\xa0"):
        with pytest.raises(ParseError):
            parse(data=data)


def test_missing_value_stays_empty_with_its_annotation_and_is_never_zero():
    text = data_of("current").decode()
    for token, note in (("", "OBS_VALUE=missing"), ("-", "OBS_VALUE=-"), ("...", "OBS_VALUE=...")):
        changed = text.replace(",A,2009,-15.423608,", f",A,2009,{token},", 1)
        assert changed != text
        obs = by(parse(data=changed.encode()), "GRC", 2009)
        assert obs.value is None and note in obs.note
        assert by(parse(data=changed.encode()), "GRC", 2008).value == Decimal("-10.314767")


def test_publication_date_outside_april_october_leaves_the_release_empty():
    text = data_of("current").decode().replace("2026-10-13T02:00:00Z", "2026-07-13T02:00:00Z")
    result = parse(data=text.encode())
    assert {(o.release_id, o.released_at) for o in result.observations} == {(None, None)}
    assert result.warnings
    empty = data_of("current").decode().replace("2026-10-13T02:00:00Z", "")
    assert {o.release_id for o in parse(data=empty.encode()).observations} == {None}


def test_vintage_must_agree_with_its_publication_date():
    text = data_of("apr").decode().replace("2026-04-14T13:00:00Z", "2026-10-13T02:00:00Z")
    with pytest.raises(ParseError, match="发布日期"):
        parse("apr", data=text.encode())


def test_two_releases_in_one_response_are_a_failure():
    lines = data_of("current").decode().splitlines(keepends=True)
    lines[3] = lines[3].replace("2026-10-13T02:00:00Z", "2026-04-14T13:00:00Z")
    with pytest.raises(ParseError, match="多个发布"):
        parse(data="".join(lines).encode())


@pytest.mark.parametrize("key,expected", [
    ("JPN+USA+DEU+FRA+GBR.NGDPD.A", (["JPN", "USA", "DEU", "FRA", "GBR"], ["NGDPD"], "A")),
    ("GRC.GGXCNL_NGDP.A", (["GRC"], ["GGXCNL_NGDP"], "A")),
])
def test_parse_key(key, expected):
    assert parse_key(key) == expected


@pytest.mark.parametrize("key", ["", None, 5, "GRC.NGDP", "GRC..A", ".NGDP.A", "GRC.NGDP.A.X", "GRC.NGDP.A+Q", "GRC+.NGDP.A",
                                 "GRC/NGDP.A", "GRC.NGDP.A?x=1", "GRC.NGDP.A#f", "GRC. NGDP.A", "GRC.日本.A", "GRC.NGDP.A\n"])
def test_parse_key_rejects_other_characters_and_shapes(key):
    with pytest.raises(ValueError):
        parse_key(key)


def test_release_of_only_names_april_and_october():
    assert release_of("2026-10-13T02:00:00Z") == ("WEO-2026-OCT", date(2026, 10, 13))
    assert release_of("2026-04-14T13:00:00Z") == ("WEO-2026-APR", date(2026, 4, 14))
    assert release_of("2025-10-14T23:30:00+09:00") == ("WEO-2025-OCT", date(2025, 10, 14))
    assert release_of("2026-10-01T00:30:00+09:00") is None  # UTC date is 2026-09-30
    for text in ("", "not a date", "2026-07-13T02:00:00Z"):
        assert release_of(text) is None
