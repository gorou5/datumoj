"""MAPLE-150: fixed-size table batches, legacy parity and measured Python peaks.

The scaling assertion fixes each table at 500 rows and doubles table count
(12,000 -> 24,000 rows). A <1.5x peak permits allocator noise while rejecting
linear growth. The largest individual table/document remains the memory bound.
"""

import gc
import hashlib
import json
import subprocess
import sys
import tracemalloc
from contextlib import closing
from dataclasses import replace

from datumoj.rikolto.index.projections import project
from datumoj.rikolto.pipeline import PipelineDeps, _backfill_projection, _projection_batches, backfill_projections
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.store.observations import DuckDBObservationStore
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeKeywordIndex, FakeManifest, FakeRawStore, FakeRegistry
from datumoj.rikolto.testing.samples import make_dataset, make_observation, make_snapshot, make_spec


def corpus(root, tables):
    snapshot = make_snapshot()
    manifest = FakeManifest()
    manifest.record_snapshot(snapshot)
    catalog = FakeCatalogStore()
    observations = DuckDBObservationStore(root)
    for index in range(tables):
        key = f"jp-test:table{index}"
        catalog.upsert([make_dataset(key, f"Synthetic table {index}", snapshot_sha256=snapshot.sha256,
                                     locator=f"entry={index}", description="Catalog text")])
        observations.write_table("jp-test", str(index), [make_observation(
            f"t{index}-o{row}", None if row % 3 else "1", note="-" if row % 3 else None,
            source_id="jp-test", dataset_key=key, table_key=str(index), snapshot_sha256=snapshot.sha256, locator=f"row={row}",
        ) for row in range(500)])
    return PipelineDeps(manifest=manifest, raw=FakeRawStore(), registry=FakeRegistry([make_spec()]),
                        catalog=catalog, observations=observations, chunks=None, keyword=FakeKeywordIndex())


def legacy_backfill(deps):
    """149's one-shot grouping, sorting, merge and original join-based hash."""
    datasets, observations = [], []
    for _, entries, rows in _projection_batches(deps):
        datasets.extend(entries)
        observations.extend(rows)
    projections = project(datasets, observations)
    projections.sort(key=lambda item: all("#projection:entry:" in c.chunk_id for c in item[1]))
    for doc, chunks in projections:
        fingerprint = hashlib.sha256("\n".join(c.model_dump_json() for c in chunks).encode()).hexdigest()
        assert doc.metadata["projection_fingerprint"] == fingerprint
        _backfill_projection(deps, doc, chunks, update_keyword=False)


def measured(operation, deps, path):
    with closing(SqliteChunkStore(path)) as chunks:
        deps = replace(deps, chunks=chunks)
        gc.collect()
        tracemalloc.start()
        try:
            operation(deps)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    return peak


def audit(tmp_path):
    small = corpus(tmp_path / "small", 24)
    large = corpus(tmp_path / "large", 48)
    # Warm the describe caches outside measurement, identically for both sizes.
    small.observations.describe()
    large.observations.describe()
    small_peak = measured(backfill_projections, small, tmp_path / "small.sqlite")
    large_peak = measured(backfill_projections, large, tmp_path / "large.sqlite")
    legacy_peak = measured(legacy_backfill, large, tmp_path / "legacy.sqlite")
    evidence = {"rows": [12000, 24000], "table_rows": 500,
                "batched_peak_bytes": [small_peak, large_peak], "legacy_peak_bytes_24000": legacy_peak,
                "doubling_ratio": round(large_peak / small_peak, 3), "maximum_ratio": 1.5}
    with closing(SqliteChunkStore(tmp_path / "large.sqlite")) as batched, closing(
        SqliteChunkStore(tmp_path / "legacy.sqlite")
    ) as legacy:
        # Compare all fields, ordering, provenance, document metadata and hashes.
        for index in range(48):
            key = f"jp-test:table{index}"
            assert batched.chunks_for(key) == legacy.chunks_for(key)
            assert batched.get_document(key) == legacy.get_document(key)
            assert batched.current_order(key) == legacy.current_order(key)
        assert backfill_projections(replace(large, chunks=batched)) == 0
    return evidence


def test_batched_peak_is_sublinear_and_below_one_shot(tmp_path, capsys):
    # Isolate the measured heap from pytest capture/plugins and other tests'
    # background threads. Use real DuckDB reads and SQLite writes in the child.
    response = subprocess.run([
        sys.executable, "-c",
        ("import json,sys; from pathlib import Path; "
        "from tests.regressions.test_audit_150 import audit; "
        "print(json.dumps(audit(Path(sys.argv[1]))))"), str(tmp_path),
    ], capture_output=True, text=True, check=True)
    evidence = json.loads(response.stdout)
    with capsys.disabled():
        print("\nAUDIT 150 MEMORY " + json.dumps(evidence, sort_keys=True))
    small_peak, large_peak = evidence["batched_peak_bytes"]
    assert large_peak < small_peak * 1.5, evidence
    assert large_peak < evidence["legacy_peak_bytes_24000"] * 0.5, evidence
