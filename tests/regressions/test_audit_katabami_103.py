"""Default-off cleanup matches the pre-103 report and deletion set exactly."""

import hashlib
import json
from contextlib import closing

import pytest

from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import PublishState
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec
from tests.regressions.test_audit_131 import DAY
from tests.test_capacity import copy, generation

NEW_FIELDS = {"by_source", "reclaimable_orphan_raw", "reclaimable_orphan_raw_bytes", "orphan_raw_enabled",
              "orphan_raw_deleted", "orphan_raw_deleted_bytes", "orphan_raw_skip_reason"}


def seed(root, manager_class=StateManager):
    root.mkdir(parents=True)
    store = FakeStateObjectStore()
    spec = make_spec(scope={"raw_expire_latest_types": ["entry"]})
    expired = make_snapshot(data=b"Synthetic expired", snapshot_id="expired", fetched_at=T0 - 10 * DAY)
    protected = make_snapshot(data=b"Synthetic protected", snapshot_id="protected", fetched_at=T0)
    with closing(SqliteManifest(root / "manifest.sqlite")) as manifest:
        for snap, kind in ((expired, "entry"), (protected, "feed")):
            manifest.record_snapshot(snap, {"resource_type": kind})
            manifest.record_publish_state(snap.snapshot_id, PublishState.published)
            store.put(f"raw/{snap.sha256[:2]}/{snap.sha256}",
                      b"Synthetic expired" if snap is expired else b"Synthetic protected")
        manifest.mark_raw_optional(expired.sha256, T0 - 4 * DAY)
    retained = copy("control.json", b"Synthetic retained")
    retired = copy("control.json", b"Synthetic retired")
    unused = copy("control.json", b"Synthetic unused")
    manifest_copy = copy("manifest.sqlite", b"Synthetic manifest", "sqlite")
    for item, data in ((retained, b"Synthetic retained"), (retired, b"Synthetic retired"),
                       (unused, b"Synthetic unused"), (manifest_copy, b"Synthetic manifest")):
        store.put(item.key, data)
    for item in (generation(1, [manifest_copy, retired], T0 - 10 * DAY),
                 generation(2, [manifest_copy, retained], T0)):
        store.put(f"generations/{item.generation}.json", item.model_dump_json().encode())
    store.put("current.json", b'{"generation":2}')
    orphan = b"Synthetic unpublished raw"
    sha = hashlib.sha256(orphan).hexdigest()
    store.put(f"raw/{sha[:2]}/{sha}", orphan)
    return manager_class(store, root), store, [spec]


# Filled from the original cleanup at 499a5f5, not from the new implementation.
BASELINE = json.loads(r'''{
  "cases": {
    "00": {
      "deleted": [],
      "updates": {}
    },
    "01": {
      "deleted": [],
      "updates": {}
    },
    "10": {
      "deleted": [
        "copies/sha256/6a9c26cac64f0c846742d9a8593199024532c934973b913fa494bbf455a76f47",
        "copies/sha256/c6632485bfcceb26e821235814aa0d3ac0c8cd3e6049d3fb79151e21b28450be",
        "generations/1.json"
      ],
      "updates": {
        "copies_deleted": 2,
        "copies_deleted_bytes": 33,
        "generations_deleted": 1,
        "total_bytes": 764
      }
    },
    "11": {
      "deleted": [
        "copies/sha256/6a9c26cac64f0c846742d9a8593199024532c934973b913fa494bbf455a76f47",
        "copies/sha256/c6632485bfcceb26e821235814aa0d3ac0c8cd3e6049d3fb79151e21b28450be",
        "generations/1.json",
        "raw/71/7104d0617beac439f88f14e3d4c2fd859ffb1f5d65780f2041fe34ada2e7b68f"
      ],
      "updates": {
        "copies_deleted": 2,
        "copies_deleted_bytes": 33,
        "generations_deleted": 1,
        "raw_deleted": 1,
        "raw_deleted_by_age": 1,
        "raw_deleted_by_age_bytes": 17,
        "raw_deleted_bytes": 17,
        "total_bytes": 747
      }
    }
  },
  "report": {
    "copies_deleted": 0,
    "copies_deleted_bytes": 0,
    "elapsed_s": 0.0,
    "enabled": false,
    "failures": 0,
    "generations_deleted": 0,
    "over_7gb": false,
    "raw_deleted": 0,
    "raw_deleted_by_age": 0,
    "raw_deleted_by_age_bytes": 0,
    "raw_deleted_by_receipt": 0,
    "raw_deleted_by_receipt_bytes": 0,
    "raw_deleted_bytes": 0,
    "raw_enabled": false,
    "raw_marked": 0,
    "receipt_status": "missing",
    "reclaimable_copies": 2,
    "reclaimable_copies_bytes": 33,
    "reclaimable_generations": 1,
    "reclaimable_raw": 1,
    "reclaimable_raw_bytes": 17,
    "timed_out": false,
    "total_bytes": 1451
  }
}
''')


@pytest.mark.parametrize("enabled,raw_enabled", [(False, False), (False, True), (True, False), (True, True)])
def test_default_off_matches_pre_103_report_and_deleted_objects(tmp_path, enabled, raw_enabled):
    manager, store, specs = seed(tmp_path / "data")
    before = set(store.list(""))
    report = manager.cleanup(specs, now=T0, check=lambda: None, enabled=enabled,
                             raw_enabled=raw_enabled, clock=lambda: 0.0)
    assert report["orphan_raw_enabled"] is False
    assert report["reclaimable_orphan_raw"] == 1 and report["orphan_raw_deleted"] == 0
    key = f"{int(enabled)}{int(raw_enabled)}"
    expected = dict(BASELINE["report"])
    expected.update(enabled=enabled, raw_enabled=raw_enabled, **BASELINE["cases"][key]["updates"])
    assert {k: v for k, v in report.items() if k not in NEW_FIELDS} == expected
    assert sorted(before - set(store.list(""))) == BASELINE["cases"][key]["deleted"]
