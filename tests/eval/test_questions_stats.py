import csv
import json
import re
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

from datumoj.eval import load_questions
from datumoj.testing.fakes import FakeObservationStore
from datumoj.testing.samples import make_observation

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
QUESTIONS = load_questions(ROOT / "eval" / "questions")
CATALOG_QUESTIONS = [q for q in QUESTIONS if q.id.startswith("catalog-")]
STAT_QUESTIONS = [q for q in QUESTIONS if q.id.startswith("stat-")]
JAPANESE = re.compile(r"[぀-ヿ一-鿿]")

JSON_CELLS = {
    "stat-dashboard-tokyo-population-2023": (
        "stat_dashboard/getData_b1_pop5x48_2021-2023CY.json",
        110,
        ("0201010000000010000", "13000", "2023CY00", "20020101"),
    ),
    "stat-dashboard-national-population-2023": (
        "stat_dashboard/getData_b1_pop5x48_2021-2023CY.json",
        97,
        ("0201010000000010000", "00000", "2023CY00", "20020101"),
    ),
    "stat-dashboard-okinawa-births-2022": (
        "stat_dashboard/getData_b1_pop5x48_2021-2023CY.json",
        240,
        ("0203010000000010000", "47000", "2022CY00", "00450011"),
    ),
    "stat-dashboard-hokkaido-deaths-2021": (
        "stat_dashboard/getData_b1_pop5x48_2021-2023CY.json",
        290,
        ("0203020000000010000", "01000", "2021CY00", "00450011"),
    ),
    "stat-dashboard-osaka-inmigrants-2023": (
        "stat_dashboard/getData_b1_pop5x48_2021-2023CY.json",
        556,
        ("0204010000000010020", "27000", "2023CY00", "00200502"),
    ),
    "stat-dashboard-tokyo-outmigrants-2023": (
        "stat_dashboard/getData_b1_pop5x48_2021-2023CY.json",
        686,
        ("0204020000000010020", "13000", "2023CY00", "00200502"),
    ),
    "stat-dashboard-tokyo-households-2020": (
        "stat_dashboard/getData_b2_census4x48_2020CY.json",
        14,
        ("0202000000000010000", "13000", "2020CY00", "00200521"),
    ),
    "stat-dashboard-national-employed-2020": (
        "stat_dashboard/getData_b2_census4x48_2020CY.json",
        98,
        ("0301100000010010000", "00000", "2020CY00", "00200502"),
    ),
    "stat-dashboard-osaka-natural-change-2020": (
        "stat_dashboard/getData_b2_census4x48_2020CY.json",
        76,
        ("0203060000000010010", "27000", "2020CY00", "00450011"),
    ),
}


def test_catalog_and_numeric_question_banks_load():
    assert len(CATALOG_QUESTIONS) == 10
    assert len(STAT_QUESTIONS) == 10
    assert set(JSON_CELLS) | {"stat-estat-tokyo-ict-employees-2016"} == {q.id for q in STAT_QUESTIONS}
    assert all(q.kind == "retrieval" and len(q.expect.hits) == 1 for q in CATALOG_QUESTIONS)
    assert all(q.kind == "numeric" and q.expect.value is not None for q in STAT_QUESTIONS)

    langs = Counter(q.lang for q in CATALOG_QUESTIONS)
    assert langs["zh"] + langs["en"] >= 3
    ckan_ids = set()
    for name in ("package_search_q-jinko_rows5_start0.json", "package_search_q-jinko_rows5_start5.json"):
        payload = json.loads((FIXTURES / "ckan_egov" / name).read_text(encoding="utf-8"))
        ckan_ids.update(item["id"] for item in payload["result"]["results"])
    for q in CATALOG_QUESTIONS:
        assert q.queries and all(JAPANESE.search(query) for query in q.queries)
        assert q.expect.hits[0].doc_key.startswith("jp-egov-data:")
        dataset_id = q.expect.hits[0].doc_key.split(":", 1)[1]
        assert dataset_id in ckan_ids
        assert "tests/fixtures/ckan_egov/" in q.notes


def test_numeric_sql_targets_observations_and_estat_measure_is_exact():
    for q in STAT_QUESTIONS:
        sql = q.expect.value.sql.lower()
        assert re.match(r"\s*select\b", sql)
        assert " from observations " in sql

    estat = next(q for q in STAT_QUESTIONS if q.id == "stat-estat-tokyo-ict-employees-2016")
    sql = estat.expect.value.sql
    assert "indicator_code = 'jsyu102A.0001'" in sql
    assert "INDICATOR LIKE" not in sql.upper()


def test_dashboard_numeric_answers_match_recorded_json_cells():
    for q in STAT_QUESTIONS:
        if q.id not in JSON_CELLS:
            continue
        relative_path, one_based_index, selector = JSON_CELLS[q.id]
        path = FIXTURES / relative_path
        assert path.is_file(), f"{q.id}: 原表不存在：{path}"
        values = json.loads(path.read_text(encoding="utf-8"))["GET_STATS"]["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"]
        row = values[one_based_index - 1]["VALUE"]
        assert tuple(row[key] for key in ("@indicator", "@regionCode", "@time", "@stat")) == selector
        assert Decimal(row["$"]) == Decimal(str(q.expect.value.equals))
        assert relative_path in q.notes
        assert f"DATA_OBJ[{one_based_index}]" in q.notes


def test_estat_numeric_answer_matches_recorded_csv_cell():
    q = next(q for q in STAT_QUESTIONS if q.id == "stat-estat-tokyo-ict-employees-2016")
    relative_path = "estat_files/000031720773_ecensus2016_t02_tokyo.csv"
    path = FIXTURES / relative_path
    assert path.is_file(), f"{q.id}: 原表不存在：{path}"
    with path.open(encoding="cp932", newline="") as source:
        rows = list(csv.reader(source))

    row = rows[976]  # 物理第 977 行
    assert row[0] == "977"
    assert row[4] == "sanD1.0963"
    assert rows[9][8] == "jsyu102A.0001"  # 表头第 10 行、第 9 列（従業者数）
    assert Decimal(row[8]) == Decimal(str(q.expect.value.equals))
    assert relative_path in q.notes
    assert "I977" in q.notes


def test_numeric_sql_returns_one_fixture_value_for_each_question():
    store = FakeObservationStore()
    by_table = {}

    for q in STAT_QUESTIONS:
        if q.id in JSON_CELLS:
            relative_path, one_based_index, (indicator, area, period, _stat) = JSON_CELLS[q.id]
            path = FIXTURES / relative_path
            raw = json.loads(path.read_text(encoding="utf-8"))["GET_STATS"]["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"][one_based_index - 1]["VALUE"]["$"]
            table_key = indicator
            observation = make_observation(
                q.id,
                raw,
                source_id="jp-stat-dashboard",
                dataset_key=f"jp-stat-dashboard:{indicator}",
                table_key=table_key,
                indicator_code=indicator,
                area_code=area,
                time_start=date(int(period[:4]), 1, 1),
                time_end=date(int(period[:4]), 12, 31),
            )
            by_table.setdefault((observation.source_id, table_key), []).append(observation)
            continue

        path = FIXTURES / "estat_files/000031720773_ecensus2016_t02_tokyo.csv"
        with path.open(encoding="cp932", newline="") as source:
            row = list(csv.reader(source))[976]
        common = dict(
            source_id="jp-estat-files",
            dataset_key="jp-estat-files:000031720773",
            table_key="000031720773",
            area_code="13000",
            time_start=date(2016, 1, 1),
            time_end=date(2016, 12, 31),
            dims={"industry_code": "sanD1.0963"},
        )
        for column, code in ((8, "jsyu102A.0001"), (9, "jsyu102A.0002"), (10, "jsyu102A.0003")):
            observation = make_observation(
                f"estat-{code}",
                row[column],
                indicator_code=code,
                **common,
            )
            by_table.setdefault((observation.source_id, "000031720773"), []).append(observation)

    for (source_id, table_key), observations in by_table.items():
        store.write_table(source_id, table_key, observations)
    for q in STAT_QUESTIONS:
        result = store.query_readonly(q.expect.value.sql)
        assert len(result.rows) == 1, q.id
        assert Decimal(str(result.rows[0][0])) == Decimal(str(q.expect.value.equals)), q.id
