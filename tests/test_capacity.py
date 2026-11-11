"""Capacity accounting from synthetic inventory and a local manifest, offline."""

import hashlib
from contextlib import closing
from datetime import timedelta

import pytest

from datumoj.rikolto.capacity import inventory
from datumoj.rikolto.core.contracts import StateObject
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import StateGeneration, StateWriter, StoreCopy
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec


def copy(name, data, fmt="bytes"):
    sha = hashlib.sha256(data).hexdigest()
    return StoreCopy(name=name, key=f"copies/sha256/{sha}", sha256=sha, size_bytes=len(data), format=fmt)


def generation(number, stores, when=T0):
    return StateGeneration(generation=number, previous_generation=number - 1 or None, created_at=when,
                           writer=StateWriter(kind="local", run_id="synthetic"), stores=stores, raw_prefix="raw",
                           vector_coverage=None, last_successful_runs={})


def obj(key, size):
    return StateObject(key, size, '"synthetic"')


@pytest.fixture
def accounting(tmp_path):
    path = tmp_path / "manifest.sqlite"
    shared = make_snapshot("jp-test", data=b"shared", snapshot_id="shared-a")
    shared_b = make_snapshot("jp-other", data=b"shared", snapshot_id="shared-b")
    owned = make_snapshot("jp-test", data=b"owned", snapshot_id="owned")
    with closing(SqliteManifest(path)) as manifest:
        owned_b = make_snapshot("jp-test", data=b"owned", snapshot_id="owned-again")
        for snapshot in (shared, shared_b, owned, owned_b):
            manifest.record_snapshot(snapshot)
    common = copy("control.json", b"same")
    old = copy("chunks.sqlite.blocks/0", b"old-block", "sqlite-block-v1")
    new = copy("manifest.sqlite.blocks/0", b"new-block", "sqlite-block-v1")
    # The checksum-only entry has no corresponding physical object.
    virtual = copy("manifest.sqlite", b"virtual-checksum", "sqlite-blocks-v1")
    vector = copy("vectors/data/secret-hash.lance", b"vector")
    generations = [generation(1, [common, old]), generation(2, [common, new, virtual, vector])]
    unused = copy("control.json", b"unused-copy-data")
    objects = [obj(c.key, c.size_bytes) for c in (common, old, new, unused, vector)]
    objects += [obj("generations/1.json", 100), obj("generations/2.json", 200),
                obj(f"raw/{shared.sha256[:2]}/{shared.sha256}", 6),
                obj(f"raw/{owned.sha256[:2]}/{owned.sha256}", 5), obj("raw/ff/unknown", 13),
                obj("mystery/raw/data", 11), obj("current.json", 16)]
    return objects, generations, path


def test_all_dimensions_are_physical_deduplicated_and_current_is_separate(accounting):
    objects, generations, manifest = accounting
    report = inventory(objects, generations, 2, manifest, [make_spec()], now=T0 + timedelta(days=10))
    total = sum(o.size_bytes for o in objects)
    assert report["scope"] == "this_bucket"
    assert report["total"] == {"objects": 12, "bytes": total}
    assert report["headroom_bytes"] == {"7gb": 7_000_000_000 - total, "8_5gb": 8_500_000_000 - total,
                                        "9gb": 9_000_000_000 - total}
    assert report["prefixes"] == {"generations": {"objects": 2, "bytes": 300},
                                  "copies": {"objects": 5, "bytes": 44},
                                  "raw": {"objects": 3, "bytes": 24}, "other": {"objects": 2, "bytes": 27}}
    assert report["generations"] == [
        {"generation": 1, "referenced_bytes": 13, "exclusive_bytes": 9},
        {"generation": 2, "referenced_bytes": 19, "exclusive_bytes": 15},
    ]
    assert report["current_generation"] == report["generations"][1]
    assert report["raw_sources"] == {"jp-test": {"objects": 1, "bytes": 5},
                                     "shared": {"objects": 1, "bytes": 6},
                                     "unreferenced": {"objects": 1, "bytes": 13}}
    assert report["stores"] == {"control.json": {"bytes": 4}, "chunks.sqlite": {"bytes": 9},
                                "manifest.sqlite": {"bytes": 9}, "vectors": {"bytes": 6}}
    assert report["reclaimable"] == {"generations": {"objects": 1, "bytes": 100},
                                     "copies": {"objects": 2, "bytes": 25},
                                     "raw": {"objects": 0, "bytes": 0}, "raw_enabled": False}


def test_retention_boundary_current_is_always_retained_and_signed_headroom(accounting):
    objects, generations, manifest = accounting
    report = inventory(objects, generations, 2, manifest, [], now=T0 + timedelta(hours=72), raw_enabled=True,
                       other_objects=[obj("outside/copies/private", 9_000_000_000)])
    assert report["reclaimable"]["generations"] == {"objects": 0, "bytes": 0}
    assert report["reclaimable"]["raw_enabled"] is True
    assert report["headroom_bytes"]["9gb"] < 0
    assert report["prefixes"]["other"] == {"objects": 3, "bytes": 9_000_000_027}
    assert report["prefixes"]["copies"]["objects"] == 5
