"""Two HTTP-failing cloud generations open an issue; a repaired generation closes it."""

import json
from datetime import timedelta

from tests.test_cloud_run import BODY, invoke
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline cloud fixture.
from tests.test_source_issues import fake_gh as fake_gh  # noqa: PLC0414 -- Fake gh executable, no network.
from tests.test_source_issues import run_script


def test_two_failed_rounds_open_issue_and_successful_round_closes(world, httpserver, fake_gh):
    httpserver.clear()
    for index in range(3):
        httpserver.expect_request(f"/source{index}.txt").respond_with_data(
            BODY, status=503 if index == 0 else 200, content_type="text/plain",
        )
    for attempt in range(1, 3):
        result, _ = invoke(world, f"failure-{attempt}")
        assert result["generation"] == attempt and result["exit_status"] == 1
        row = next(row for row in result["source_health"] if row["source_id"] == "jp-cloud0")
        assert row["categories"] == {"http_error": 1}
        assert row["consecutive_failures"] == attempt
        assert run_script(world.tmp, result["source_health"], result["generation"]).returncode == 0
        state = json.loads(fake_gh.read_text())
        assert len(state["issues"]) == attempt - 1
        world.clock[0] += timedelta(hours=1)
    assert state["issues"][0]["title"] == "[source-broken] jp-cloud0"

    httpserver.clear()
    for index in range(3):
        httpserver.expect_request(f"/source{index}.txt").respond_with_data(BODY, content_type="text/plain")
    result, _ = invoke(world, "repaired")
    assert result["generation"] == 3 and result["exit_status"] == 0
    row = next(row for row in result["source_health"] if row["source_id"] == "jp-cloud0")
    assert row["status"] == "ok" and row["consecutive_failures"] == 0 and row["first_failure_at"] is None
    assert run_script(world.tmp, result["source_health"], result["generation"]).returncode == 0
    state = json.loads(fake_gh.read_text())
    assert len(state["issues"]) == 1 and state["issues"][0]["closed"]
    assert state["issues"][0]["comments"] == ["已恢复（第 3 代）"]
    assert all(value not in json.dumps(state["calls"]) for value in ("://", "?token=", "private body", "synthetic-secret"))
