from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError, discover_parsers
from datumoj.rikolto.core.models import TimeGranularity
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.parsers.wto_bulk import WTOBulkParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
ZIP = ROOT / "tests/fixtures/wto_bulk/merchandise_values_annual_dataset_excerpt.zip"
URL = "https://stats.wto.org/assets/UserGuide/merchandise_values_annual_dataset.zip"
LAST_MODIFIED = "Wed, 28 Oct 2026 19:55:37 GMT"
ETAG = '"468d724d1667dd1:0"'
SPEC = load_registry(ROOT / "sources").get("intl-wto")
assert SPEC is not None
META = {"request_type": "bulk", "indicators": ["ITS_MTV_AX", "ITS_MTV_AM"], "partner": "000", "product": "TO",
        "reporters": ["840", "156", "276", "250", "392", "410"], "start_year": 2015, "end_year": 2024}


def sample(data=None, meta=None, **kw):
    data = ZIP.read_bytes() if data is None else data
    kw = {"etag": ETAG, "last_modified": LAST_MODIFIED, **kw}
    return ParseSample(make_snapshot("intl-wto", URL, data, **kw), data, META if meta is None else meta)


def parse(data=None, meta=None, **kw):
    s = sample(data, meta, **kw)
    result = WTOBulkParser().parse(s.snapshot, s.data, s.meta)
    result.check_against(s.snapshot)
    return result


def zipped(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.csv", text.encode("cp1252"))
    return buffer.getvalue()


class TestWTOBulkParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return WTOBulkParser()

    @pytest.fixture
    def samples(self):
        return [sample()]

    @pytest.fixture
    def bad_samples(self):
        return [b"not a zip", b"", zipped("a,b\r\n1,2\r\n")]


def test_discovered_and_spec_license():
    assert discover_parsers()[("wto_bulk", "*")] is WTOBulkParser
    lic = SPEC.license
    assert (lic.usage_scope.value, lic.redistribution.value, lic.outflow.value) == ("redistributable", "allowed", "raw_ok")
    assert lic.cloud_model_ok is False
    assert "WTO" in lic.attribution and "ODbL" in lic.attribution


def test_six_reporters_both_flows_all_years_and_scope_filter():
    result = parse()
    assert len(result.observations) == 120  # the four out-of-scope rows are dropped
    cells = {(o.area_code, o.indicator_code, o.time_label) for o in result.observations}
    assert len(cells) == 120
    for reporter in ("250", "410"):  # France and Korea
        for code in ("ITS_MTV_AX", "ITS_MTV_AM"):
            assert {y for r, c, y in cells if r == reporter and c == code} == {str(y) for y in range(2015, 2025)}
    assert not {o.area_code for o in result.observations} & {"678", "686"}
    assert all(o.dims["product"] == "TO" and o.dims["partner"] == "000" for o in result.observations)
    assert all(o.unit == "Million US dollar" and o.note is None for o in result.observations)
    obs = next(o for o in result.observations if o.area_code == "840" and o.indicator_code == "ITS_MTV_AM"
               and o.time_label == "2023")
    assert obs.value == Decimal(3168237) and obs.granularity is TimeGranularity.year
    assert (obs.time_start, obs.time_end) == (date(2023, 1, 1), date(2023, 12, 31))
    assert "SITC3" in result.datasets[0].description and "Comtrade" in result.datasets[0].description


def test_release_comes_from_the_zip_headers_only():
    result = parse()
    sha = make_snapshot("intl-wto", URL, ZIP.read_bytes()).sha256
    assert {o.release_id for o in result.observations} == {f"{LAST_MODIFIED}|{ETAG}|{sha}"}
    assert {o.released_at for o in result.observations} == {date(2026, 10, 28)}
    bare = parse(etag=None)
    assert all(o.release_id is None and o.released_at is None for o in bare.observations)
    assert bare.warnings


def test_cp1252_decoding_and_break_flag_kept():
    meta = {**META, "reporters": [*META["reporters"], "678", "686"], "start_year": 2000}
    result = parse(meta=meta)
    stp = next(o for o in result.observations if o.area_code == "678")
    assert stp.area_label == "Sao Tomé and Principe" and stp.value == Decimal(103)
    sen = next(o for o in result.observations if o.area_code == "686")
    assert sen.value == Decimal(3498) and sen.note == "ValueFlagCode=B (Break in series)"


HEADER = ("IndicatorCode,Indicator,ReporterCode,Reporter,PartnerCode,ProductClassificationCode,"
          "ProductCode,Unit,Year,ValueFlagCode,ValueFlag,Value")


def test_estimate_flag_and_missing_value_never_zero():
    rows = ["ITS_MTV_AX,Exports,840,USA,000,SITC3,TO,Million US dollar,2024,E,Estimated,1234",
            "ITS_MTV_AX,Exports,840,USA,000,SITC3,TO,Million US dollar,2023,,,",
            "ITS_MTV_AX,Exports,840,USA,000,SITC3,TO,Million US dollar,2022,,,x"]
    result = parse(zipped("\r\n".join([HEADER, *rows]) + "\r\n"))
    by_year = {o.time_label: o for o in result.observations}
    assert by_year["2024"].value == Decimal(1234) and by_year["2024"].note == "ValueFlagCode=E (Estimated)"
    assert by_year["2023"].value is None and by_year["2023"].note == "Value=missing"
    assert by_year["2022"].value is None and by_year["2022"].note == "Value=x"


@pytest.mark.parametrize("value", ["abc", "NaN"])
def test_invalid_value_fails(value):
    row = f"ITS_MTV_AX,Exports,840,USA,000,SITC3,TO,Million US dollar,2024,,,{value}"
    with pytest.raises(ParseError):
        parse(zipped(f"{HEADER}\r\n{row}\r\n"))


def test_missing_scope_meta_fails():
    with pytest.raises(ParseError, match="scope"):
        parse(meta={})
