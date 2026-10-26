from __future__ import annotations

import copy
import json
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from datumoj.rikolto.core.contracts import CrawlContext, ParseError, discover_crawlers, discover_parsers
from datumoj.rikolto.core.models import TimeGranularity
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.crawlers.stat_dashboard import StatDashboardCrawler, plan_batches
from datumoj.rikolto.parsers.stat_dashboard import StatDashboardParser
from datumoj.rikolto.reference.areas import lookup
from datumoj.rikolto.testing.contract_suites import CrawlerSuite, ParserSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import T0, ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "stat_dashboard"
SPEC = load_registry(ROOT / "sources").get("jp-stat-dashboard")
assert SPEC is not None
CATALOG = (FIXTURES / "getIndicatorInfo_all.synthetic.json").read_bytes()
B1 = (FIXTURES / "getData_b1_pop5x48_2021-2023CY.json").read_bytes()
B2 = (FIXTURES / "getData_b2_census4x48_2020CY.json").read_bytes()
B3 = (FIXTURES / "getData_b3_gdp_fy2019-2022_modifiedFrom.json").read_bytes()
API = "https://dashboard.e-stat.go.jp/api/1.0/Json/"


def _samples() -> list[ParseSample]:
    contents = [
        ("getIndicatorInfo", CATALOG, {"request_type": "catalog"}),
        ("getData", B1, {"request_type": "data"}),
        ("getData", B2, {"request_type": "data"}),
        ("getData", B3, {"request_type": "data", "ModifiedFrom": "20250101"}),
    ]
    return [
        ParseSample(make_snapshot(SPEC.id, f"{API}{path}", data), data, meta)
        for path, data, meta in contents
    ]


def _with_modified_from(url: str, value: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode([*parse_qsl(parts.query), ("modifiedFrom", value)]), parts.fragment))


def _routes() -> dict[str, FakeResponse]:
    ctx = CrawlContext(spec=SPEC, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
    tasks = list(StatDashboardCrawler().plan(ctx))
    responses = [CATALOG, B1, B2]
    routes: dict[str, FakeResponse] = {}
    for index, task in enumerate(tasks):
        body = responses[index]
        response = FakeResponse(200, body, {"Content-Type": "application/json; charset=UTF-8"})
        routes[task.url] = response
        if task.meta.get("request_type") == "data":
            routes[_with_modified_from(task.url, T0.strftime("%Y%m%d"))] = response
    return routes


class TestStatDashboardCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return StatDashboardCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher(_routes(), now=lambda: T0)


class TestStatDashboardParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return StatDashboardParser()

    @pytest.fixture
    def samples(self):
        return _samples()

    @pytest.fixture
    def bad_samples(self):
        return [b"not json", b"{}", b'{"GET_STATS":{"RESULT":{"status":"0"}}}']


def test_plugins_are_discovered():
    assert discover_crawlers()["stat_dashboard"] is StatDashboardCrawler
    assert discover_parsers()[("stat_dashboard", "*")] is StatDashboardParser


def test_batch_planner_boundaries():
    exact = plan_batches([f"i{i}" for i in range(5)], [f"r{i}" for i in range(50)])
    assert len(exact) == 1
    assert len(exact[0][0]) == 5 and len(exact[0][1]) == 50

    oversized = plan_batches([f"i{i}" for i in range(6)], [f"r{i}" for i in range(51)])
    assert len(oversized) == 4
    assert all(0 < len(indicators) <= 5 and 0 < len(regions) <= 50 for indicators, regions in oversized)
    assert plan_batches([], ["r1"]) == []
    assert plan_batches(["i1"], []) == []


def test_plan_fetches_unfiltered_directory_and_all_48_regions():
    ctx = CrawlContext(spec=SPEC, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
    tasks = list(StatDashboardCrawler().plan(ctx))
    catalog_params = parse_qs(urlsplit(tasks[0].url).query)
    assert catalog_params == {"Lang": ["JP"]}
    assert tasks[0].meta == {"request_type": "catalog"}

    data_tasks = tasks[1:]
    assert len(data_tasks) == 2
    seen_indicators = []
    seen_regions = set()
    for task in data_tasks:
        params = parse_qs(urlsplit(task.url).query)
        indicators = params["IndicatorCode"][0].split(",")
        regions = params["RegionCode"][0].split(",")
        assert len(indicators) <= 5 and len(regions) <= 50
        seen_indicators.extend(indicators)
        seen_regions.update(regions)
    assert seen_indicators == SPEC.scope["indicators"]
    assert seen_regions == {"00000", *(f"{number:02d}000" for number in range(1, 48))}


def test_incremental_requests_keep_full_response_when_modified_from_is_echoed():
    manifest, raw = FakeManifest(), FakeRawStore()
    fetcher = FakeHttpFetcher(_routes(), now=lambda: T0)
    crawler = StatDashboardCrawler()
    first = drive_crawler(crawler, SPEC, fetcher, manifest=manifest, raw=raw, now=T0)
    first_data = [step for step in first if step.task.meta.get("request_type") == "data"]
    assert first_data and all("modifiedFrom" not in parse_qs(urlsplit(step.task.url).query) for step in first_data)

    second = drive_crawler(crawler, SPEC, fetcher, manifest=manifest, raw=raw, now=T0 + timedelta(days=1))
    second_data = [step for step in second if step.task.meta.get("request_type") == "data"]
    assert len(second_data) == len(first_data)
    assert all(parse_qs(urlsplit(step.task.url).query)["modifiedFrom"] == [T0.strftime("%Y%m%d")] for step in second_data)
    assert [len(json.loads(step.result.content)["GET_STATS"]["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"]) for step in second_data] == [720, 145]


def test_unfiltered_synthetic_directory_becomes_datasets():
    sample = _samples()[0]
    result = StatDashboardParser().parse(sample.snapshot, sample.data, sample.meta)
    source_records = json.loads(sample.data)["GET_META_INDICATOR_INF"]["METADATA_INF"]["CLASS_INF"]["CLASS_OBJ"]
    assert len(source_records) == 30
    assert len(result.datasets) == len(source_records)
    assert result.datasets[0].dataset_key == f"{SPEC.id}:{source_records[0]['@code']}"
    assert result.datasets[0].title == source_records[0]["@name"]
    assert result.datasets[0].extras["indicator_code"] == source_records[0]["@code"]


def test_each_record_in_recorded_data_samples_converts_exactly():
    parser = StatDashboardParser()
    for sample in _samples()[1:]:
        payload = json.loads(sample.data)["GET_STATS"]["STATISTICAL_DATA"]
        records = payload["DATA_INF"]["DATA_OBJ"]
        parsed = parser.parse(sample.snapshot, sample.data, sample.meta)
        by_locator = {observation.locator: observation for observation in parsed.observations}
        assert len(parsed.observations) == len(records)
        indicators, times, units = {}, {}, {}
        for group in payload["CLASS_INF"]["CLASS_OBJ"]:
            values = {
                item["@code"]: item.get("@name", item.get("$"))
                for item in group.get("CLASS", [])
                if isinstance(item, dict) and isinstance(item.get("@code"), str)
            }
            {"indicator": indicators, "time": times, "unit": units}.get(group.get("@id"), {}).update(values)
        surveys = {item["@code"]: item["$"] for item in payload["TABLE_INF"]["STAT_NAME"]}

        for index, record in enumerate(records, 1):
            row = record["VALUE"]
            observation = by_locator[f"row={index}"]
            code, area = row["@indicator"], lookup(row["@regionCode"])[0]
            assert observation.indicator_code == code
            assert observation.indicator == indicators[code]
            assert observation.area_code == area.code5
            assert observation.area_label == area.label
            assert observation.time_label == times[row["@time"]]
            assert observation.unit == units[row["@unit"]]
            assert observation.survey == surveys[row["@stat"]]
            if row["@time"].endswith("FY00"):
                year = int(row["@time"][:4])
                assert observation.granularity is TimeGranularity.fiscal_year
                assert observation.time_start.isoformat() == f"{year}-04-01"
                assert observation.time_end.isoformat() == f"{year + 1}-03-31"
            elif row["@time"].endswith("CY00"):
                year = int(row["@time"][:4])
                assert observation.granularity is TimeGranularity.year
                assert observation.time_start.isoformat() == f"{year}-01-01"
                assert observation.time_end.isoformat() == f"{year}-12-31"
            if re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", row["$"]):
                assert observation.value == Decimal(row["$"])
                assert observation.note is None
            else:
                assert observation.value is None and observation.note == row["$"]


def test_partial_indicator_data_stays_missing_instead_of_becoming_zero():
    sample = _samples()[2]
    parsed = StatDashboardParser().parse(sample.snapshot, sample.data, sample.meta)
    social_change = [observation for observation in parsed.observations if observation.indicator_code == "0204050000000010020"]
    assert [observation.area_code for observation in social_change] == ["00000"]
    assert len(parsed.observations) == 145


def test_obs_ids_are_stable_and_duplicates_are_removed_only_within_one_response():
    parser = StatDashboardParser()
    sample = _samples()[3]
    echoed_parameters = json.loads(sample.data)["GET_STATS"]["PARAMETER"]
    assert echoed_parameters["ModifiedFrom"] == "20250101"
    assert "modifiedFrom" not in echoed_parameters
    first = parser.parse(sample.snapshot, sample.data, sample.meta)
    later_snapshot = sample.snapshot.model_copy(update={"fetched_at": sample.snapshot.fetched_at + timedelta(days=1)})
    later = parser.parse(later_snapshot, sample.data, sample.meta)
    assert len(later.observations) == len(first.observations) == 192
    assert [item.obs_id for item in later.observations] == [item.obs_id for item in first.observations]

    duplicate_payload = json.loads(B2)
    duplicate_rows = duplicate_payload["GET_STATS"]["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"]
    duplicate_rows.append(copy.deepcopy(duplicate_rows[0]))
    duplicate_bytes = json.dumps(duplicate_payload, ensure_ascii=False).encode("utf-8")
    duplicate_snapshot = make_snapshot(SPEC.id, f"{API}getData", duplicate_bytes)
    deduplicated = parser.parse(duplicate_snapshot, duplicate_bytes)
    assert len(deduplicated.observations) == 145
    assert len({item.obs_id for item in deduplicated.observations}) == len(deduplicated.observations)

    conflicting_payload = json.loads(B2)
    conflicting_rows = conflicting_payload["GET_STATS"]["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"]
    conflicting_rows.append(copy.deepcopy(conflicting_rows[0]))
    conflicting_rows[-1]["VALUE"]["$"] = str(int(conflicting_rows[-1]["VALUE"]["$"]) + 1)
    conflicting_bytes = json.dumps(conflicting_payload, ensure_ascii=False).encode("utf-8")
    conflicting_snapshot = make_snapshot(SPEC.id, f"{API}getData", conflicting_bytes)
    with pytest.raises(ParseError):
        parser.parse(conflicting_snapshot, conflicting_bytes)

    changed_payload = json.loads(B3)
    changed_rows = changed_payload["GET_STATS"]["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"]
    changed_rows[0]["VALUE"]["$"] = str(int(changed_rows[0]["VALUE"]["$"]) + 1)
    changed_bytes = json.dumps(changed_payload, ensure_ascii=False).encode("utf-8")
    changed_snapshot = make_snapshot(SPEC.id, f"{API}getData", changed_bytes)
    changed = parser.parse(changed_snapshot, changed_bytes)
    assert changed.observations[0].obs_id != first.observations[0].obs_id
    assert [item.obs_id for item in changed.observations[1:]] == [item.obs_id for item in first.observations[1:]]


@pytest.mark.parametrize("marker", ["-", "…", "x", "秘匿"])
def test_suppression_markers_remain_notes_without_zero_fill(marker):
    payload = json.loads(B2)
    payload["GET_STATS"]["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"][0]["VALUE"]["$"] = marker
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    snapshot = make_snapshot(SPEC.id, f"{API}getData", data)
    observation = StatDashboardParser().parse(snapshot, data).observations[0]
    assert observation.value is None and observation.note == marker

