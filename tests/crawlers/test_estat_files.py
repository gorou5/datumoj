from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

import pytest
from openpyxl import load_workbook

from datumoj.core.contracts import CrawlContext, ParseError, ParseResult, discover_crawlers, discover_parsers
from datumoj.core.registry import load_registry
from datumoj.crawlers.estat_files import EstatFilesCrawler
from datumoj.parsers.estat_tables import EstatTableParser
from datumoj.testing.contract_suites import CrawlerSuite, ParserSuite
from datumoj.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.testing.samples import T0, ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "estat_files"
SPEC = load_registry(ROOT / "sources").get("jp-estat-files")
DOWNLOAD = "https://www.e-stat.go.jp/stat-search/file-download?statInfId={}&fileKind={}"

XLSX_ID, TOKYO_ID, OSAKA_ID = "000013168605", "000031720773", "000031721271"
XLSX = (FIXTURES / "000013168605_jinsui_pref_population_2000-2020.xlsx").read_bytes()
TOKYO = (FIXTURES / "000031720773_ecensus2016_t02_tokyo.csv").read_bytes()
OSAKA = (FIXTURES / "000031721271_ecensus2016_t02_osaka.csv").read_bytes()
URLS = {XLSX_ID: DOWNLOAD.format(XLSX_ID, 4), TOKYO_ID: DOWNLOAD.format(TOKYO_ID, 1), OSAKA_ID: DOWNLOAD.format(OSAKA_ID, 1)}

# 录制时的响应头：CSV 头写 UTF-8（实际 cp932），东京和大阪的下载名都是 02000.csv
CSV_HEADERS = {"Content-Type": "text/csv;charset=UTF-8", "Content-Disposition": "attachment; filename*=UTF-8''02000.csv"}
XLSX_HEADERS = {"Content-Type": "application/octet-stream", "Content-Disposition": "attachment; filename*=UTF-8''05k5-5.xlsx"}


def _routes(tokyo: bytes = TOKYO) -> dict[str, FakeResponse]:
    return {
        URLS[XLSX_ID]: FakeResponse(200, XLSX, XLSX_HEADERS),
        URLS[TOKYO_ID]: FakeResponse(200, tokyo, CSV_HEADERS),
        URLS[OSAKA_ID]: FakeResponse(200, OSAKA, CSV_HEADERS),
    }


def _crawl(routes: dict[str, FakeResponse]) -> dict[str, ParseSample]:
    """用假执行器抓一遍，返回 {statInfId: 快照、raw 字节、manifest 里的 meta}。"""
    manifest, raw = FakeManifest(), FakeRawStore()
    steps = drive_crawler(EstatFilesCrawler(), SPEC, FakeHttpFetcher(routes, now=lambda: T0), manifest=manifest, raw=raw)
    return {
        s.task.meta["stat_inf_id"]: ParseSample(s.snapshot, raw.get(s.snapshot.sha256), manifest.snapshot_meta(s.snapshot.snapshot_id))
        for s in steps
    }


@cache
def _parsed(stat_inf_id: str) -> ParseResult:
    s = _crawl(_routes())[stat_inf_id]
    return EstatTableParser().parse(s.snapshot, s.data, s.meta)


def _by_locator(stat_inf_id: str) -> dict[str, object]:
    return {o.locator: o for o in _parsed(stat_inf_id).observations}


def _meta(stat_inf_id: str, **changes: object) -> dict[str, object]:
    entry = next(t for t in SPEC.scope["tables"] if t["stat_inf_id"] == stat_inf_id)
    return {"area_code": None, **entry, **changes}


def _parse(stat_inf_id: str, data: bytes | None = None, **changes: object) -> ParseResult:
    data = data if data is not None else {XLSX_ID: XLSX, TOKYO_ID: TOKYO, OSAKA_ID: OSAKA}[stat_inf_id]
    snap = make_snapshot("jp-estat-files", URLS[stat_inf_id], data)
    return EstatTableParser().parse(snap, data, _meta(stat_inf_id, **changes))


# ---------- 合格测试 ----------


class TestEstatFilesCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return EstatFilesCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher(_routes(), now=lambda: T0)


class TestEstatTableParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return EstatTableParser()

    @pytest.fixture
    def samples(self):
        return list(_crawl(_routes()).values())

    @pytest.fixture
    def bad_samples(self):
        return [b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00", XLSX[:2000], b"<html><body>error</body></html>"]


# ---------- 采集 ----------


def test_plan_follows_manifest_and_file_kind():
    ctx = CrawlContext(spec=SPEC, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
    tasks = list(EstatFilesCrawler().plan(ctx))
    assert [t.url for t in tasks] == [URLS[XLSX_ID], URLS[TOKYO_ID], URLS[OSAKA_ID]]  # XLSX 用 fileKind=4
    for task, entry in zip(tasks, SPEC.scope["tables"], strict=True):
        assert {k: v for k, v in task.meta.items() if v is not None} == entry
    limited = CrawlContext(spec=SPEC, manifest=FakeManifest(), raw=FakeRawStore(), now=T0, limit=1)
    assert [t.url for t in EstatFilesCrawler().plan(limited)] == [URLS[XLSX_ID]]


def test_plugins_are_discovered():
    assert discover_crawlers()["estat_files"] is EstatFilesCrawler
    assert discover_parsers()[("estat_files", "*")] is EstatTableParser


def test_manifest_entries_have_required_fields():
    for entry in SPEC.scope["tables"]:
        assert {"stat_inf_id", "survey", "title", "layout", "expected_area_level"} <= set(entry)


@pytest.mark.parametrize("change", [
    {"layout": "long_with_dims"},
    {"layout": "census_area_by_measure"},  # 缺 area_code
    {"stat_inf_id": 13168605},
    {"survey": " "},
])
def test_plan_rejects_bad_manifest(change):
    tables = [{**SPEC.scope["tables"][0], **change}]
    ctx = CrawlContext(spec=SPEC.model_copy(update={"scope": {"tables": tables}}), manifest=FakeManifest(),
                       raw=FakeRawStore(), now=T0)
    with pytest.raises(ValueError):
        list(EstatFilesCrawler().plan(ctx))


# ---------- wide_area_by_year（人口推計 XLSX，单位千人） ----------

S1, S2 = "総人口（2000年～2015年）", "日本人人口（2000年～2015年）"
S3, S4 = "総人口 (2015年～2020年)", "日本人人口 (2015年～2020年)"


@pytest.mark.parametrize("sheet,cell,value", [
    (S1, "E11", 126926), (S1, "P11", 127834), (S1, "T11", 127095), (S1, "E23", 5926), (S1, "O38", 8865),
    (S1, "T58", 1434), (S1, "E24", 12064), (S2, "E11", 125613), (S3, "J11", 126146), (S3, "J23", 6284),
    (S3, "J37", 2578), (S4, "J24", 13484),
])
def test_wide_values_match_source_cells(sheet, cell, value):
    obs = _by_locator(XLSX_ID)[f"sheet={sheet};cell={cell}"]
    assert obs.value == Decimal(value)
    assert (obs.unit, obs.survey, obs.granularity.value) == ("千人", "人口推計", "year")


def test_wide_dimensions_and_dedup_of_2015():
    result = _parsed(XLSX_ID)
    obs = {(o.indicator, o.area_code, o.time_label): o for o in result.observations}
    assert len(result.observations) == len(obs) == 2 * 48 * 21  # 两个指标 × 48 地区 × 2000–2020
    chiba = obs[("総人口", "12000", "2015年")]
    assert (chiba.value, chiba.area_label, chiba.locator) == (Decimal(6223), "千葉県", f"sheet={S1};cell=T23")
    assert (chiba.time_start, chiba.time_end) == (date(2015, 10, 1), date(2015, 10, 1))
    assert chiba.note == "注1)" and obs[("総人口", "12000", "2016年")].note is None
    assert obs[("日本人人口", "13000", "2020年")].value == Decimal(13484)
    assert obs[("総人口", "00000", "2011年")].area_label == "全国"
    assert not any(o.locator.startswith(f"sheet={S3};cell=E") for o in result.observations)
    assert any(S1 in w and S3 in w and "48" in w for w in result.warnings)


def test_wide_rejects_conflicting_duplicate_year():
    # 同一 XLSX 里 2015 年两组表的值必须一致；把第 3 表千葉県 2015 年（E23 = 6223）改掉就要报错
    wb = load_workbook(io.BytesIO(XLSX))
    wb[S3]["E23"] = 6224
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ParseError, match="不一致"):
        _parse(XLSX_ID, buf.getvalue())


# ---------- census_area_by_measure（経済センサス cp932 CSV，响应头称 UTF-8） ----------


@pytest.mark.parametrize("cell,value", [
    ("H14", "621671"), ("I14", "9005511"), ("J14", "5277921"), ("K14", "3669914"), ("AF14", "14.5"),
    ("H15", "467"), ("L15", None), ("H977", "21935"), ("I977", "849374"), ("H1065", "150728"),
    ("I1767", "802679"), ("AF1947", "29.3"),
])
def test_census_tokyo_values(cell, value):
    obs = _by_locator(TOKYO_ID)[f"cell={cell};row={cell.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"]
    assert obs.value == (None if value is None else Decimal(value))
    assert (obs.area_code, obs.area_label) == ("13000", "東京都")
    if value is None:
        assert obs.note == "-"


@pytest.mark.parametrize("cell,value", [
    ("H14", "392940"), ("I14", "4393139"), ("L14", "149755"), ("AF14", "11.2"), ("H15", "292"), ("L15", None),
    ("H977", "5842"), ("I1065", "1002387"), ("H1767", "34357"), ("H1947", "22"),
])
def test_census_osaka_values(cell, value):
    obs = _by_locator(OSAKA_ID)[f"cell={cell};row={cell.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"]
    assert obs.value == (None if value is None else Decimal(value))
    assert (obs.area_code, obs.area_label) == ("27000", "大阪府")
    if value is None:
        assert obs.note == "-"


def test_census_dimensions():
    result = _parsed(TOKYO_ID)
    assert len(result.observations) == (1947 - 13) * 25
    by = _by_locator(TOKYO_ID)
    individual = by["cell=L15;row=15"]
    assert individual.indicator == "事業所数" and individual.indicator_code == "jsyu102A.0000"
    assert individual.dims == {
        "industry": "A～B 農林漁業", "industry_code": "sanD1.0001", "industry_level": "1",
        "legal_org": "個人", "legal_org_code": "keiso4A.0001",
    }
    assert individual.survey == "平成28年経済センサス‐活動調査"
    assert (individual.time_label, individual.time_start, individual.time_end) == ("平成28年", date(2016, 1, 1), date(2016, 12, 31))
    employees = by["cell=I14;row=14"]
    assert (employees.indicator, employees.unit, employees.dims["legal_org"]) == ("従業者数(人)", "人", "総数(経営組織)")
    per = by["cell=AF14;row=14"]
    assert (per.indicator, per.indicator_code, per.unit) == ("１事業所当たり従業者数(人)", "jsyu102B.0000", "人")
    assert "legal_org" not in per.dims


# ---------- 核对：地区、调查名、表题、版式 ----------


def test_file_belonging_to_another_prefecture_fails():
    # 东京的 statInfId 下载回来的却是大阪的文件：下载名同为 02000.csv，只能从内容发现
    sample = _crawl(_routes(tokyo=OSAKA))[TOKYO_ID]
    assert sample.meta["area_code"] == "13000"
    with pytest.raises(ParseError, match="27000"):
        EstatTableParser().parse(sample.snapshot, sample.data, sample.meta)


@pytest.mark.parametrize("stat_inf_id,change", [
    (XLSX_ID, {"survey": "平成28年経済センサス‐活動調査"}),
    (XLSX_ID, {"survey": "国勢調査"}),
    (TOKYO_ID, {"survey": "人口推計"}),
    (TOKYO_ID, {"survey": "令和3年経済センサス‐活動調査"}),
])
def test_survey_mismatch_fails(stat_inf_id, change):
    with pytest.raises(ParseError, match="调查"):
        _parse(stat_inf_id, **change)


@pytest.mark.parametrize("stat_inf_id,title", [
    (XLSX_ID, "第4表 都道府県別人口"),
    (TOKYO_ID, "第１表　産業（細分類）別民営事業所数"),
])
def test_title_mismatch_fails(stat_inf_id, title):
    with pytest.raises(ParseError, match="表题"):
        _parse(stat_inf_id, title=title)


@pytest.mark.parametrize("stat_inf_id,change", [
    (XLSX_ID, {"layout": "long_with_dims"}),
    (XLSX_ID, {"layout": "census_area_by_measure", "area_code": "13000"}),
    (TOKYO_ID, {"layout": "wide_area_by_year", "area_code": None}),
    (TOKYO_ID, {"expected_area_level": "municipality"}),
])
def test_unknown_or_mismatched_layout_fails(stat_inf_id, change):
    with pytest.raises(ParseError):
        _parse(stat_inf_id, **change)


def test_missing_meta_fails():
    snap = make_snapshot("jp-estat-files", URLS[TOKYO_ID], TOKYO)
    with pytest.raises(ParseError):
        EstatTableParser().parse(snap, TOKYO, None)


def test_snapshot_url_must_match_manifest_entry():
    snap = make_snapshot("jp-estat-files", URLS[OSAKA_ID], TOKYO)
    with pytest.raises(ParseError, match="statInfId"):
        EstatTableParser().parse(snap, TOKYO, _meta(TOKYO_ID))


def test_unrecognized_value_fails():
    # 第 15 行第 8 列（H15 = 467）换成认不出的值
    bad = TOKYO.replace(b'"467"', b'"4x7"', 1) if b'"467"' in TOKYO else TOKYO.replace(b",467,", b",4x7,", 1)
    assert bad != TOKYO
    with pytest.raises(ParseError, match="H15"):
        _parse(TOKYO_ID, bad)
