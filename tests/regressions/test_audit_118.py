from __future__ import annotations

from datetime import timedelta

import pytest

from datumoj.admin.web import _schedule_label
from datumoj.core.monitor import evaluate_cloud_alerts
from datumoj.core.schedule import next_due
from datumoj.testing.samples import T0, make_spec


def test_weekly_schedule_is_consistent_across_due_monitoring_and_admin():
    assert next_due("weekly", None, T0) == T0
    assert next_due("weekly", T0, T0) == T0 + timedelta(days=7)
    assert next_due("manual", T0, T0) is None
    assert next_due("", T0, T0) is None
    with pytest.raises(ValueError, match="不支持"):
        next_due("yearly", None, T0)

    weekly = make_spec("jp-weekly", schedule="weekly")
    success_at = T0 - timedelta(days=7)
    success = {weekly.id: {"run_id": "weekly-run", "finished_at": success_at.isoformat()}}
    assert evaluate_cloud_alerts(
        [weekly], {weekly.id}, success, T0 - timedelta(hours=1), T0,
    ) == []

    success[weekly.id]["finished_at"] = (success_at - timedelta(seconds=1)).isoformat()
    alerts = evaluate_cloud_alerts(
        [weekly], {weekly.id}, success, T0 - timedelta(hours=1), T0,
    )
    assert [alert.rule for alert in alerts] == ["cloud_run_missing"]
    assert alerts[0].details["expected_after_seconds"] == 604800
    assert _schedule_label("weekly") == "每周"
