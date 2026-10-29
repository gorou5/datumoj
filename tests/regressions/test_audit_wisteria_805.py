"""805 audit: all output surfaces, authoritative permissions and offline samples."""

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from datumoj.eltiro.models import OutputManifest, load_demand
from datumoj.eltiro.package import package_run
from datumoj.eltiro.testing.fakes import FakeDemandRegistry
from tests.eltiro.test_cli import cli, test_package_command_success_and_safe_license_denial
from tests.eltiro.test_executor import consumer
from tests.eltiro.test_package import (
    sources,
    test_complete_multi_input_lineage_strictest_level_and_requested_symbols_only,
    test_denial_before_first_delivery_does_not_create_outbox,
    test_derived_only_never_copies_source_text_anywhere_in_bundle,
    test_diff_tracks_only_same_consumer_demand_across_versions_and_delivery_order,
    test_failed_new_delivery_preserves_previous_package_even_after_rename,
    test_failure_cleans_staging_and_new_outbox_directories,
    test_gap_report_merges_only_pinned_demand_and_safe_structures,
    test_license_downgrade_cannot_return_existing_raw_text_bundle,
    test_mixed_rows_remove_only_restricted_text_and_profile_copies_cannot_grant,
    test_raw_bundle_exact_decimal_manifest_provenance_and_idempotency,
    test_registry_and_acceptance_deny_whole_bundle_without_values,
    test_symlinks_traversal_and_other_consumer_overlap_are_rejected,
)
from tests.regressions.test_audit_wisteria_804 import sample_result


def package_sample(root, sample):
    (rows, citations, gaps), record = sample_result(root, sample)
    demand = load_demand(Path("docs/plans/0.4-wisteria-samples") / sample)
    source_ids = sorted({i["source_id"] for c in citations for i in c["citation"]["inputs"]})
    # Synthetic host registry explicitly grants these licenses, never the saved profile copies.
    registry = sources({s: "raw_ok" if n == 0 else "derived_only" for n, s in enumerate(source_ids)})
    config = consumer(source_ids, id=demand.consumer, models={"consumer": demand.consumer,
                      "authorized_sources": source_ids}, accept_outflow=["raw_ok", "derived_only"])
    demands = FakeDemandRegistry()
    demands.register(demand)
    result = package_run(record["run_id"], data_root=root, registry=registry,
                         consumers={config.id: config}, demands=demands)
    return result, record, demand, rows


@pytest.mark.parametrize("sample", ["three-gdp-reconciliation.yaml", "greece-deficit-revision-chain.yaml"])
def test_two_sample_demands_run_to_package(tmp_path, sample):
    result, record, demand, run_rows = package_sample(tmp_path, sample)
    path = result["path"]
    rows = pq.read_table(path / "data.parquet").to_pylist()
    assert rows and result["row_count"] == sum(r["symbol"] in demand.output.fields for r in run_rows)
    assert set(pq.read_table(path / "data.parquet").column_names) == set(demand.output.fields)
    manifest = OutputManifest.model_validate_json((path / "manifest.json").read_bytes())
    assert manifest.generation_token == record["generation_token"] and manifest.models == []
    provenance = json.loads((path / "provenance.json").read_bytes())
    for output_row, provenance_row in zip(rows, provenance, strict=True):
        entry = manifest.citation_index[output_row["citation_id"]]
        assert entry.inputs and output_row["citation_id"] == provenance_row["citation_id"]
        assert all(set(i.model_dump()) == {"source_id", "table_key", "obs_id", "snapshot_sha256", "release_id"}
                   for i in entry.inputs)
    assert any(entry.operation_chain for entry in manifest.citation_index.values())
    assert len(list(path.iterdir())) == result["file_count"]
