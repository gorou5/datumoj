import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from openpyxl import load_workbook

from datumoj.rikolto.core.contracts import CrawlContext, ParseError, SourceSpec
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.estat_files import EstatFilesCrawler
from datumoj.rikolto.parsers.estat_tables import EstatTableParser
from datumoj.rikolto.testing.contract_suites import CrawlerSuite, ParserSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse
from datumoj.rikolto.testing.samples import T0, ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
SPEC = SourceSpec.model_validate(yaml.safe_load((ROOT / "sources/jp-census.yaml").read_text()))
ENTRY = SPEC.scope["tables"][0]
URL = "https://www.e-stat.go.jp/stat-search/file-download?statInfId=000040389320&fileKind=0"
DATA = (ROOT / "tests/fixtures/census2024/c1_001_1.xlsx").read_bytes()


def sample(data=DATA, meta=None):
    return ParseSample(make_snapshot("jp-census", URL, data, content_type="text/plain; charset=cp932"),
                       data, ENTRY if meta is None else meta)


def parse(data=DATA, meta=None):
    s = sample(data, meta)
    return EstatTableParser().parse(s.snapshot, s.data, s.meta)


class TestCensusParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return EstatTableParser()

    @pytest.fixture
    def samples(self):
        return [sample()]

    @pytest.fixture
    def bad_samples(self):
        return [b"not Excel", DATA[:100]]


class TestCensusCrawler(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return EstatFilesCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher({URL: FakeResponse(200, DATA)}, now=lambda: T0)


def test_plan_uses_recorded_download_kind():
    task, = EstatFilesCrawler().plan(CrawlContext(SPEC, FakeManifest(), FakeRawStore(), T0))
    assert task.url == URL and task.meta["stat_inf_id"] == "000040389320"
    assert SPEC.schedule == "weekly" and SPEC.rate_per_sec == 0.2
    assert "2024 年甲调查排除没有雇员的个人经营事业所，和往年口径不同" in SPEC.license.notes
    fetcher = FakeHttpFetcher({URL: FakeResponse(200, b"x" * (25 * 1024 * 1024 + 1))}, now=lambda: T0)
    assert EstatFilesCrawler().classify(task, fetcher.fetch(URL)) is FetchOutcome.too_large


@pytest.mark.parametrize("code,label,row,values", [
    ("41000", "佐賀県", 1744, (30919, 26628, 338267)),
    ("41201", "佐賀市", 1745, (10379, 8780, 108887)),
    ("00000", "全国", 8, (5060494, 4023941, 56285043)),
])
def test_values_match_source(code, label, row, values):
    result = parse()
    obs = [o for o in result.observations if o.area_code == code]
    assert len(obs) == 3
    assert [o.value for o in obs] == [Decimal(v) for v in values]
    assert [o.unit for o in obs] == ["事業所", "事業所", "人"]
    assert [o.locator for o in obs] == [f"sheet=c1_001_1;cell={c}{row}" for c in "CDE"]
    assert [o.indicator for o in obs] == ["事業所数（事業内容等不詳を含む）", "事業所数", "従業者数"]
    assert all(o.area_label == label and o.time_label == "令和６年" and o.time_start == date(2024, 1, 1)
               and o.time_end == date(2024, 12, 31) and o.note == "雇用者のいない個人経営の事業所を除く" for o in obs)
    assert len(result.observations) == 1962 * 3
    assert all(len(o.area_code) == 5 and o.area_code.isdigit() for o in result.observations)


def modified(tmp_path, updates):
    book = load_workbook(io.BytesIO(DATA))
    for cell, value in updates.items():
        book.active[cell] = value
    path = tmp_path / "census.synthetic.xlsx"
    book.save(path)
    book.close()
    return path.read_bytes()


@pytest.mark.parametrize("mark", ["-", "x", "…", "秘匿"])
def test_special_values_never_become_zero(tmp_path, mark):
    # Captured table has no special values. Change one cell only in a synthetic copy.
    result = parse(modified(tmp_path, {"D1745": mark}))
    obs, = [o for o in result.observations if o.locator == "sheet=c1_001_1;cell=D1745"]
    assert obs.value is None and obs.note == f"{mark}; 雇用者のいない個人経営の事業所を除く"


@pytest.mark.parametrize("updates", [{"A1": "令和６年経済センサス‐基礎調査 乙調査"}, {"E6": "千人"},
                                     {"B1745": "412010_佐賀市"}, {"B1745": "41000_佐賀県"},
                                     {"D1745": "garbage"}, {"B1745": ""}, {"A1745": "unknown"}])
def test_invalid_layout_does_not_silently_drop_data(tmp_path, updates):
    with pytest.raises(ParseError):
        parse(modified(tmp_path, updates))


def test_metadata_and_size_mismatch_fail():
    with pytest.raises(ParseError):
        parse(meta={**ENTRY, "stat_inf_id": "000040389329"})
    with pytest.raises(ParseError):
        parse(meta={**ENTRY, "expected_area_level": "prefecture"})
    with pytest.raises(ParseError, match="25 MiB"):
        parse(b"x" * (25 * 1024 * 1024 + 1))
