"""The capacity section changes nothing else in the weekly report and stays inside the 1 MB bound."""

import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from datumoj.rikolto import cloud
from datumoj.rikolto.analysis.reports import _capacity_lines, _period, weekly_report
from datumoj.rikolto.capacity import WEEKLY_REPORT_RESERVE
from tests.test_cloud_reports import _deps

START = datetime(2026, 11, 9, tzinfo=UTC)
DAYS = (date(2026, 11, 9), date(2026, 11, 15))


def write_rows(path):
    (path / "capacity.jsonl").write_text("".join(json.dumps({
        "recorded_at": (START + timedelta(days=day)).isoformat(), "total_bytes": 2_000_000_000 + day * 10**8,
        "raw_bytes_by_source": {"jp-test": 10**6 * day}, "db_store_bytes": 7,
    }) + "\n" for day in range(4)), encoding="utf-8")


def test_other_sections_are_byte_identical_to_before(tmp_path):
    deps = _deps(tmp_path)
    write_rows(tmp_path)
    before = weekly_report(SimpleNamespace(**{k: v for k, v in vars(deps).items() if k != "data_dir"}), *DAYS)
    after = weekly_report(deps, *DAYS)
    assert "## 容量" not in before and "## 容量" in after
    assert after.split("\n\n## 容量\n")[0] + "\n" == before
    # The whole output: the old report, a separating blank line, the expected section, nothing else.
    section = "\n".join(_capacity_lines(deps, *_period(*DAYS))).rstrip() + "\n"
    assert section.startswith("## 容量\n") and "距 8.5 GB（冻结线）" in section
    assert after == before + "\n" + section


def test_section_sits_before_the_truncation_notice_and_the_bound_holds(tmp_path):
    deps = _deps(tmp_path)
    write_rows(tmp_path)
    section = "\n".join(_capacity_lines(deps, START, START + timedelta(days=7))) + "\n"
    event = f"- 摘要：{'あ' * 300}\n"
    content = f"# 周报\n{event * 3000}\n{section}"
    assert len(content.encode()) > WEEKLY_REPORT_RESERVE
    data = cloud._weekly_report_bytes(content)
    text = data.decode()
    assert len(data) <= WEEKLY_REPORT_RESERVE
    assert section.rstrip("\n") in text
    assert text.index("## 容量") < text.index("报告超过 1 MB") and text.endswith("条事件。\n")
    omitted = int(text.rsplit("省略 ", 1)[1].split(" 条")[0])
    kept = text.count(event)  # whole events only; a cut-off one would not match
    assert text.count("- 摘要：") == kept and kept + omitted == 3000
    # A short report is stored untouched.
    short = f"# 周报\n- 摘要：x\n\n{section}"
    assert cloud._weekly_report_bytes(short) == short.encode()
