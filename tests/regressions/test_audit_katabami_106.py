"""Without a receipt, 104's protection and marker rules retain their deletion set."""

from contextlib import closing
from datetime import timedelta

import pytest

from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import PublishState
from datumoj.rikolto.core.state import RETENTION_WINDOW, plan_cleanup
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec
from tests.regressions.test_audit_131 import raw_key
from tests.test_capacity import copy, generation, obj


@pytest.mark.parametrize("former_days", [1, 7, 30])
def test_no_receipt_only_changes_age_threshold(tmp_path, former_days):
    path = tmp_path / "manifest.sqlite"
    specs = [make_spec(scope={"raw_retention_days": former_days, "raw_expire_latest_types": ["entry"]})]
    ages = {"old": 49, "boundary": 48, "young": 47, "latest": 100,
            "pending": 100, "failed": 100, "staged": 100, "required": 100, "young-mark": 100}
    snapshots = {}
    with closing(SqliteManifest(path)) as manifest:
        for name, hours in ages.items():
            snap = make_snapshot(snapshot_id=name, data=name.encode(), fetched_at=T0 - timedelta(hours=hours),
                                 url=f"https://example.test/{name}")
            snapshots[name] = snap
            manifest.record_snapshot(snap, {"resource_type": "feed" if name == "latest" else "entry"})
            if name != "pending":
                manifest.record_publish_state(snap.snapshot_id,
                                              PublishState(name) if name in {"failed", "staged"}
                                              else PublishState.published)
            if name != "required":
                manifest.mark_raw_optional(snap.sha256,
                                           T0 - (RETENTION_WINDOW / 2 if name == "young-mark" else RETENTION_WINDOW))
    objects = [obj(raw_key(snap), snap.size) for snap in snapshots.values()]
    current = generation(1, [copy("control.json", b"synthetic")], T0)
    plan = plan_cleanup(objects, [current], 1, path, specs, now=T0)
    # The source limit applies below the unchanged 48h ceiling.
    expected = {raw_key(snapshots[name]) for name, age in ages.items()
                if age >= min(former_days * 24, 48) and name in {"old", "boundary", "young"}}
    assert {item.key for item in plan["raw"]} == expected
    assert expected == {raw_key(snapshots["old"]), raw_key(snapshots["boundary"])} | (
        {raw_key(snapshots["young"])} if former_days < 2 else set()
    )
