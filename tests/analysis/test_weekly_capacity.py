"""The weekly "容量" section: pure summary, rounding and rendering from capacity.jsonl alone."""

import json
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

import pytest

from datumoj.rikolto.analysis.reports import _num, weekly_report
from datumoj.rikolto.capacity import (
    REASON_INSUFFICIENT,
    REASON_UNAVAILABLE,
    parse_capacity_rows,
    summarize_capacity,
)
from tests.test_cloud_reports import _deps

START = datetime(2026, 11, 9, tzinfo=UTC)
END = datetime(2026, 11, 15, 23, 59, 59, tzinfo=UTC)
GB = 1_000_000_000
MB = 1_000_000


def row(days, total, raw=None, db=0):
    return {"recorded_at": (START + timedelta(days=days)).isoformat(), "total_bytes": total,
            "raw_bytes_by_source": raw or {}, "db_store_bytes": db}


def write(path, rows):
    (path / "capacity.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def section(tmp_path, rows=None):
    if rows is not None:
        write(tmp_path, rows)
    text = weekly_report(_deps(tmp_path), date(2026, 11, 9), date(2026, 11, 15), ())
    return text.split("## 容量\n", 1)[1]


def three_rows():
    return [row(0, 2 * GB, {"jp-a": GB, "jp-b": 500 * MB}, 100 * MB),
            row(3, 2_600_000_000, {"jp-a": 1_500_000_000, "jp-b": 500 * MB}, 120 * MB),
            row(6, 3_200_000_000, {"jp-a": 2_400_000_000, "jp-b": 490 * MB}, 150 * MB)]


def test_three_rows_growth_and_prediction():
    result = summarize_capacity(three_rows(), START, END)
    assert result["covered_days"] == 6 and result["rows"] == 3
    assert [(s["source"], s["bytes"], s["growth"]) for s in result["sources"]] == [
        ("jp-a", 2_400_000_000, 1_400_000_000), ("jp-b", 490 * MB, -10 * MB)]
    assert result["growth_bytes"] == 1_200_000_000 and result["db_growth"] == 50 * MB
    # 1.2 GB / 6 d = 0.2 GB/d; (8.5 - 3.2) / 0.2 = 26.5 -> 27 (always rounded up).
    assert result["prediction"] == {"kind": "days", "days": 27}
    assert result["headroom_bytes"]["8.5"] == 5_300_000_000


def test_render_three_rows(tmp_path):
    text = section(tmp_path, three_rows())
    assert "摘要覆盖 6.0 天（3 行），不足 7 天" in text
    assert "当前总量：3.20 GB；区间增长 +1.20 GB" in text
    assert "距 8.5 GB（冻结线）：还差 5.30 GB" in text
    assert "约 27 天后碰到 8.5 GB 冻结线" in text
    # Source IDs go through the same Markdown escaping as the rest of the report.
    assert "| jp\\-a | 2400.00 | +1400.00 |" in text and "| jp\\-b | 490.00 | -10.00 |" in text
    assert "数据库存储副本合计：150.00 MB；区间增长 +50.00 MB" in text


def test_seven_days_of_data_is_not_flagged_short(tmp_path):
    write(tmp_path, [row(0, GB), row(7, 2 * GB)])
    text = weekly_report(_deps(tmp_path), date(2026, 11, 9), date(2026, 11, 16), ())
    assert "摘要覆盖 7.0 天（2 行）。" in text and "不足 7 天" not in text


@pytest.mark.parametrize("last", [2 * GB, GB])  # zero growth, then negative growth
def test_zero_or_negative_growth_never_hits(tmp_path, last):
    text = section(tmp_path, [row(0, 2 * GB), row(5, last)])
    line = text.split("碰线预测：")[1].split("\n")[0]
    assert line == "按当前增速不会碰线" and "天" not in line


def test_already_over_freeze_line(tmp_path):
    text = section(tmp_path, [row(0, 8 * GB), row(5, 8_600_000_000)])
    assert "已达到或超过冻结线" in text and "距 8.5 GB（冻结线）：已超出 0.10 GB" in text
    # Over the line wins even when the trend is falling.
    assert "已达到或超过冻结线" in section(tmp_path, [row(0, 9 * GB), row(5, 8_700_000_000)])


def test_fewer_than_two_rows_in_window(tmp_path):
    outside = {**row(0, GB), "recorded_at": (START - timedelta(days=1)).isoformat()}
    text = section(tmp_path, [outside, row(1, 2 * GB)])
    assert f"容量数据不足（原因码：{REASON_INSUFFICIENT}）" in text and "有 1 行" in text


def test_missing_and_unreadable_file(tmp_path):
    assert f"容量数据取不到（原因码：{REASON_UNAVAILABLE}）" in section(tmp_path)
    (tmp_path / "capacity.jsonl").mkdir()
    assert f"容量数据取不到（原因码：{REASON_UNAVAILABLE}）" in section(tmp_path)


def test_bad_lines_are_skipped(tmp_path):
    good = json.dumps(row(0, GB)).encode()
    data = b"\n".join([b"not json", good, b"\xff\xfe", b'{"recorded_at": "2026-11-10"}', b"[1]",
                       json.dumps({**row(1, GB), "total_bytes": True}).encode(),
                       json.dumps({**row(1, GB), "recorded_at": "2026-11-10T00:00:00"}).encode(),
                       json.dumps(row(5, 2 * GB)).encode(), b""])
    assert len(parse_capacity_rows(data)) == 2
    (tmp_path / "capacity.jsonl").write_bytes(data)
    assert "摘要覆盖 5.0 天（2 行）" in section(tmp_path)


def test_top_ten_and_merged_rest():
    raw_old = {f"jp-{i:02d}": 0 for i in range(12)}
    raw_new = {f"jp-{i:02d}": (i + 1) * MB for i in range(12)}
    result = summarize_capacity([row(0, GB, raw_old), row(5, 2 * GB, raw_new)], START, END)
    assert [s["source"] for s in result["sources"]] == [f"jp-{i:02d}" for i in range(11, 1, -1)]
    assert result["other"] == {"count": 2, "bytes": 3 * MB, "growth": 3 * MB}


def test_rounding_is_half_away_from_zero():
    assert _num(Fraction(1, 200), 2) == "0.01" and _num(Fraction(1, 400), 2) == "0.00"
    assert _num(Fraction(-1, 200), 2, signed=True) == "-0.01" and _num(Fraction(-1, 400), 2, signed=True) == "0.00"
    assert _num(Fraction(1, 3), 1, signed=True) == "+0.3" and _num(Fraction(-25, 10), 1) == "-2.5"
    assert _num(Fraction(95, 1000), 1) == "0.1" and _num(0, 2, signed=True) == "0.00"


def test_section_is_before_eltiro_and_free_of_identifiers(tmp_path):
    write(tmp_path, three_rows())
    deps = _deps(tmp_path)
    plain = weekly_report(deps, date(2026, 11, 9), date(2026, 11, 15), ())
    # An overview that really renders the Eltiro section (not the "no data directory" notice).
    overview = {"schema": "eltiro.overview/v1", "errors": [],
                "demands": [{"demand_id": "example-demand", "demand_version": 1, "consumer": "example-consumer"}]}
    with_eltiro = weekly_report(deps, date(2026, 11, 9), date(2026, 11, 15), (), overview)
    assert with_eltiro.startswith(plain) and "## 容量" in plain
    assert "## Eltiro" in with_eltiro and "## 容量" in with_eltiro
    assert with_eltiro.index("## 容量") < with_eltiro.index("## Eltiro")
    assert "### 在用需求（1）" in with_eltiro.split("## Eltiro", 1)[1]
    assert "bucket" not in plain.lower() and "endpoint" not in plain.lower()


def test_headroom_wording_distinguishes_reached_short_and_over(tmp_path):
    reached = section(tmp_path, [row(0, 8 * GB), row(5, 8_500_000_000)])
    assert reached.split("距 8.5 GB（冻结线）：")[1].split("\n")[0] == "已达到"
    assert "已达到或超过冻结线" in reached  # the prediction state includes equality


def test_too_deeply_nested_line_is_skipped_and_later_rows_are_read():
    deep = b"[" * 100_000 + b"]" * 100_000
    data = b"\n".join([json.dumps(row(0, GB)).encode(), deep, json.dumps(row(5, 2 * GB)).encode()])
    assert len(parse_capacity_rows(data)) == 2
