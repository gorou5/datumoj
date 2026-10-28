"""Synthetic bundles exercise real RunStore bytes and all export surfaces offline."""

from datetime import date
from decimal import Decimal
from hashlib import sha256
import json

import pyarrow.parquet as pq
import pytest

from datumoj.eltiro import package
from datumoj.eltiro.license_gate import LicenseGateError
from datumoj.eltiro.models import OutputManifest
from datumoj.eltiro.runs import RunStore
from datumoj.eltiro.testing.fakes import FakeDemandRegistry
from datumoj.eltiro.testing.samples import make_demand
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import make_spec
from tests.eltiro.test_executor import consumer

MARKER = "SYNTHETIC_RESTRICTED_SOURCE_TEXT"


def sources(levels):
    return FakeRegistry([make_spec(source, license={"outflow": level, **(
        {"usage_scope": "redistributable", "redistribution": "allowed"} if level == "raw_ok" else {})})
                         for source, level in levels.items()])


def context(root, level="raw_ok", *, demand=None, config=None):
    root.mkdir(parents=True, exist_ok=True)
    demand = demand or make_demand(output={"fields": ["country", "year", "source", "gdp", "note", "citation_id"],
                                          "format": ["csv", "parquet"]},
                                   accept_outflow=["raw_ok", "derived_only"])
    config = config or consumer(accept_outflow=["raw_ok", "derived_only"])
    demands = FakeDemandRegistry()
    demands.register(demand)
    return {"data_root": root, "registry": sources({"intl-wdi": level}),
            "consumers": {config.id: config}, "demands": demands}


def write_run(root, values=("123456789.123456789123456789",), *, demand=None, inputs=None, symbols=None, started=None):
    demand = demand or make_demand()
    rows, citations = [], []
    for i, value in enumerate(values):
        key = f"row-{i}"
        rows.append({"citation_id": key, "symbol": symbols[i] if symbols else "gdp", "country": "JPN",
                     "region_scheme": None, "region": None, "period": date(2020 + i, 1, 1),
                     "source": "intl-wdi", "series": MARKER + " INDICATOR", "release_id": "R1",
                     "released_at": None, "release_position": 1, "unit": MARKER + " UNIT",
                     "value": Decimal(value) if value is not None else None,
                     "note": MARKER + " NOTE", "spread_range": None})
        citations.append({"citation_id": key, "citation": {"inputs": inputs or [{
            "source_id": "intl-wdi", "table_key": "gdp", "obs_id": f"obs-{i}",
            "snapshot_sha256": "ab" * 32, "release_id": "R1"}], "operation_chain": []},
                          "steps": [{"evidence": MARKER, "note": MARKER}]})
    return RunStore(root).write({
        "demand_id": demand.id, "demand_version": demand.version, "generation": 1,
        "generation_token": "g1-" + "ab" * 32, "started_at": started or "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z", "models": [],
        "bindings": [{"license": {"outflow": "raw_ok"}, "profile": MARKER}], "layout": "long/v1",
    }, rows, citations, [{"symbol": "gdp", "reason": "missing_input", "details": MARKER},
                         {"symbol": "foreign", "reason": "missing_input", "consumer": "foreign-consumer"}],
        validate=lambda: None)


def delivered(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} if root.exists() else {}


def test_raw_bundle_exact_decimal_manifest_provenance_and_idempotency(tmp_path):
    ctx = context(tmp_path)
    record = write_run(tmp_path)
    result = package.package_run(record["run_id"], **ctx)
    path = result["path"]
    assert path == tmp_path / "consumer/outbox/example-demand/v1" / record["run_id"]
    assert result["row_count"] == 1 and result["file_count"] == 7 and result["gap_count"] == 1
    table = pq.read_table(path / "data.parquet")
    assert table.column_names == ["country", "year", "source", "gdp", "note", "citation_id"]
    assert table.to_pylist()[0]["note"] == MARKER + " NOTE"
    assert table.to_pylist()[0]["gdp"] == Decimal("123456789.123456789123456789")
    assert "123456789.123456789123456789" in (path / "data.csv").read_text()
    manifest = OutputManifest.model_validate_json((path / "manifest.json").read_bytes())
    assert manifest.models == [] and manifest.license_summary.outflow == "raw_ok"
    assert manifest.run_at.isoformat() == "2026-01-01T00:00:00+00:00"
    for item in manifest.files:
        assert item.sha256 == sha256((path / item.name).read_bytes()).hexdigest()
    provenance = json.loads((path / "provenance.json").read_bytes())
    assert manifest.citation_index[provenance[0]["citation_id"]].inputs[0].obs_id == "obs-0"
    assert json.loads((path / "diff.json").read_bytes())["comparable"] is False
    before = delivered(tmp_path / "consumer/outbox")
    times = {p: p.stat().st_mtime_ns for p in path.iterdir()}
    assert package.package_run(record["run_id"], **ctx) == result
    assert delivered(tmp_path / "consumer/outbox") == before
    assert {p: p.stat().st_mtime_ns for p in path.iterdir()} == times
    assert not (tmp_path / "other/outbox").exists()


def test_derived_only_never_copies_source_text_anywhere_in_bundle(tmp_path):
    demand = make_demand(output={"fields": ["country", "year", "source", "gdp", "unit", "series", "note", "citation_id"],
                                 "format": ["csv", "parquet"]}, accept_outflow=["derived_only"])
    ctx = context(tmp_path, "derived_only", demand=demand)
    record = write_run(tmp_path, values=(None, "0.000000000000000001"))
    path = package.package_run(record["run_id"], **ctx)["path"]
    assert "note" not in pq.read_table(path / "data.parquet").column_names
    assert "series" not in pq.read_table(path / "data.parquet").column_names
    assert pq.read_table(path / "data.parquet").to_pylist()[0]["unit"] == MARKER + " UNIT"
    for file in path.iterdir():
        assert (MARKER + " INDICATOR").encode() not in file.read_bytes()
        assert (MARKER + " NOTE").encode() not in file.read_bytes()
    assert pq.read_table(path / "data.parquet").to_pylist()[0]["gdp"] is None
    manifest = OutputManifest.model_validate_json((path / "manifest.json").read_bytes())
    assert manifest.license_summary.by_source == {"intl-wdi": "derived_only"}
    assert json.loads((tmp_path / "eltiro/runs" / (record["run_id"] + ".package-audit.json")).read_bytes())["status"] == "allowed"


@pytest.mark.parametrize("failure", ["no_outflow", "missing_registry", "demand", "consumer", "source"])
def test_registry_and_acceptance_deny_whole_bundle_without_values(tmp_path, failure):
    ctx = context(tmp_path, "no_outflow" if failure == "no_outflow" else "derived_only")
    if failure == "missing_registry":
        ctx["registry"] = sources({})
    if failure == "demand":
        demand = make_demand(accept_outflow=["raw_ok"])
        ctx["demands"] = FakeDemandRegistry()
        ctx["demands"].register(demand)
    if failure == "consumer":
        ctx["consumers"] = {"example-consumer": consumer(accept_outflow=["raw_ok"])}
    if failure == "source":
        ctx["consumers"] = {"example-consumer": consumer(("intl-imf-weo",), accept_outflow=["derived_only"])}
    record = write_run(tmp_path)
    existing = tmp_path / "consumer/outbox/existing.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("previous delivery")
    before = delivered(existing.parent)
    with pytest.raises(LicenseGateError) as exc:
        package.package_run(record["run_id"], **ctx)
    assert exc.value.sources == ("intl-wdi",) and MARKER not in str(exc.value)
    assert delivered(existing.parent) == before
    audit = json.loads((tmp_path / "eltiro/runs" / (record["run_id"] + ".package-audit.json")).read_bytes())
    assert audit == {"status": "blocked", "code": exc.value.code, "sources": ["intl-wdi"]}


def test_complete_multi_input_lineage_strictest_level_and_requested_symbols_only(tmp_path):
    demand = make_demand(operations=[{"id": "delta", "op": "diff", "left": "gdp", "right": "gdp"}],
                         output={"fields": ["country", "delta", "citation_id", "note"], "format": ["parquet"]},
                         accept_outflow=["raw_ok", "derived_only"])
    ctx = context(tmp_path, demand=demand)
    ctx["registry"] = sources({"intl-wdi": "raw_ok", "intl-imf-weo": "derived_only", "eu-eurostat": "no_outflow"})
    ctx["consumers"] = {"example-consumer": consumer(("intl-wdi", "intl-imf-weo", "eu-eurostat"),
                                                    accept_outflow=["raw_ok", "derived_only"])}
    inputs = [{"source_id": s, "table_key": f"table-{n}", "obs_id": "same-id", "snapshot_sha256": str(n + 1) * 64,
               "release_id": f"R{n}"} for n, s in enumerate(("intl-wdi", "intl-imf-weo"))]
    record = write_run(tmp_path, demand=demand, values=("10", "20"), symbols=["gdp", "delta"], inputs=inputs)
    citation_file = tmp_path / "eltiro/runs" / record["run_id"] / "citations.json"
    citations = json.loads(citation_file.read_bytes())
    citations[0]["citation"]["inputs"] = [{**inputs[0], "source_id": "eu-eurostat"}]
    citations[1]["citation"]["operation_chain"] = [demand.operations[0].model_dump()]
    citation_file.write_text(json.dumps(citations))
    path = package.package_run(record["run_id"], **ctx)["path"]
    rows = pq.read_table(path / "data.parquet").to_pylist()
    assert len(rows) == 1 and rows[0]["delta"] == Decimal(20) and "note" not in rows[0]
    manifest = OutputManifest.model_validate_json((path / "manifest.json").read_bytes())
    citation = manifest.citation_index[rows[0]["citation_id"]]
    assert [i.model_dump() for i in citation.inputs] == inputs
    assert citation.operation_chain == demand.operations
    assert "eu-eurostat" not in (path / "manifest.json").read_text()
    ctx["registry"] = sources({"intl-wdi": "raw_ok", "intl-imf-weo": "no_outflow"})
    with pytest.raises(LicenseGateError, match="no_outflow"):
        package.package_run(record["run_id"], **ctx)  # Current registry rechecked even on repeat.


@pytest.mark.parametrize("phase", ["write", "mkdir", "rename"])
def test_failure_cleans_staging_and_new_outbox_directories(tmp_path, monkeypatch, phase):
    ctx = context(tmp_path)
    record = write_run(tmp_path)
    outbox = tmp_path / "consumer/outbox"
    outbox.mkdir(parents=True)
    (outbox / "previous.txt").write_text("immutable")
    before = delivered(outbox)
    if phase == "write":
        real = package._write
        def fail(directory, name, content):
            if name == "manifest.json":
                raise OSError("synthetic disk failure")
            return real(directory, name, content)
        monkeypatch.setattr(package, "_write", fail)
    elif phase == "mkdir":
        real = package.os.mkdir
        def fail(name, *args, **kwargs):
            if name == "v1":
                raise OSError("synthetic mkdir failure")
            return real(name, *args, **kwargs)
        monkeypatch.setattr(package.os, "mkdir", fail)
    else:
        monkeypatch.setattr(package.os, "rename", lambda *a, **kw: (_ for _ in ()).throw(OSError("synthetic rename failure")))
    with pytest.raises(OSError):
        package.package_run(record["run_id"], **ctx)
    assert delivered(outbox) == before and sorted(p.name for p in outbox.iterdir()) == ["previous.txt"]
    assert not list((tmp_path / "eltiro/runs").glob(".package-*"))


@pytest.mark.parametrize("location", ["outbox", "parent", "run", "file", "escape", "overlap"])
def test_symlinks_traversal_and_other_consumer_overlap_are_rejected(tmp_path, location):
    ctx = context(tmp_path)
    record = write_run(tmp_path)
    other = tmp_path / "other/outbox"
    other.mkdir(parents=True)
    (other / "sentinel").write_text("unchanged")
    before = delivered(other)
    if location == "outbox":
        (tmp_path / "consumer").mkdir()
        (tmp_path / "consumer/outbox").symlink_to(other, target_is_directory=True)
    elif location == "parent":
        (tmp_path / "consumer/outbox").mkdir(parents=True)
        (tmp_path / "consumer/outbox/example-demand").symlink_to(other, target_is_directory=True)
    elif location == "run":
        run = tmp_path / "eltiro/runs" / record["run_id"]
        run.rename(run.with_name("original"))
        run.symlink_to(run.with_name("original"), target_is_directory=True)
    elif location == "file":
        file = tmp_path / "eltiro/runs" / record["run_id"] / "citations.json"
        file.rename(file.with_name("original.json"))
        file.symlink_to(file.with_name("original.json"))
    elif location == "escape":
        ctx["consumers"] = {"example-consumer": consumer(outbox="../outside")}
    else:
        ctx["consumers"]["other-consumer"] = consumer(id="other-consumer", inbox="other/inbox", outbox="consumer/outbox/other",
                                                       models={"consumer": "other-consumer", "authorized_sources": ["intl-wdi"]})
    with pytest.raises((ValueError, OSError)):
        package.package_run(record["run_id"], **ctx)
    assert delivered(other) == before


def test_diff_tracks_only_same_consumer_demand_across_versions_and_delivery_order(tmp_path):
    ctx = context(tmp_path)
    first = write_run(tmp_path, values=("10", "20", "30"), started="2026-02-01T00:00:00Z")
    package.package_run(first["run_id"], **ctx)
    # Unrelated demand in the same outbox must not become the baseline.
    other_demand = make_demand(id="another-demand", accept_outflow=["raw_ok"])
    ctx["demands"].register(other_demand)
    other = write_run(tmp_path, values=("999",), demand=other_demand)
    package.package_run(other["run_id"], **ctx)
    # Other consumer: distinct demand/config/outbox; no reads or writes in its outbox thereafter.
    foreign = make_demand(id="foreign-demand", consumer="foreign-consumer", accept_outflow=["raw_ok"])
    config = consumer(id=foreign.consumer, inbox="foreign/inbox", outbox="foreign/outbox",
                      models={"consumer": foreign.consumer, "authorized_sources": ["intl-wdi"]},
                      accept_outflow=["raw_ok"])
    ctx["demands"].register(foreign)
    ctx["consumers"][config.id] = config
    foreign_run = write_run(tmp_path, demand=foreign)
    package.package_run(foreign_run["run_id"], **ctx)
    foreign_before = delivered(tmp_path / config.outbox)
    version = make_demand(version=2, accept_outflow=["raw_ok"], output={"fields": ["gdp", "citation_id"], "format": ["csv"]})
    ctx["demands"].register(version)
    second = write_run(tmp_path, values=("11", "20", "30", "40"), demand=version, started="2026-01-01T00:00:00Z")
    path = package.package_run(second["run_id"], **ctx)["path"]
    diff = json.loads((path / "diff.json").read_bytes())
    assert diff["previous_run_id"] == first["run_id"] and len(diff["changed"]) == 1 and len(diff["added"]) == 1
    assert diff["changed"][0]["before"]["value"] == "10" and diff["changed"][0]["after"]["value"] == "11"
    manifest = OutputManifest.model_validate_json((path / "manifest.json").read_bytes())
    assert set(manifest.citation_index) == {"row_0", "row_1", "row_2", "row_3", "previous_row_0"}
    assert manifest.citation_index[diff["changed"][0]["before"]["citation_id"]].inputs
    third = write_run(tmp_path, values=("11",), demand=version)
    path = package.package_run(third["run_id"], **ctx)["path"]
    diff = json.loads((path / "diff.json").read_bytes())
    assert diff["previous_run_id"] == second["run_id"] and len(diff["deleted"]) == 3 and diff["changed"] == []
    assert delivered(tmp_path / config.outbox) == foreign_before


def test_gap_report_merges_only_pinned_demand_and_safe_structures(tmp_path):
    from datumoj.eltiro.gaps import gap_report
    from datumoj.eltiro.testing.fakes import FakeMatchStore, FakeProfileStore
    ctx = context(tmp_path, "derived_only")
    demand = ctx["demands"].get("example-demand", 1)
    gap_report(demand, 1, consumer=ctx["consumers"][demand.consumer], matches=FakeMatchStore(),
               profiles=FakeProfileStore(), registry=ctx["registry"], data_dir=tmp_path)
    # An unrelated report must never be picked up.
    (tmp_path / "eltiro/gaps/foreign-v1-g1.json").write_text('"FOREIGN_CONSUMER_TEXT"')
    record = write_run(tmp_path)
    path = package.package_run(record["run_id"], **ctx)["path"]
    gaps = json.loads((path / "gaps.json").read_bytes())
    assert gaps["run"] == [{"symbol": "gdp", "reason": "missing_input"}]
    assert gaps["report"][0]["gaps"] == ["below_min_sources", "missing"]
    assert "FOREIGN" not in (path / "gaps.json").read_text()
    assert MARKER not in (path / "gaps.json").read_text()


def test_license_downgrade_cannot_return_existing_raw_text_bundle(tmp_path):
    ctx = context(tmp_path)
    record = write_run(tmp_path)
    result = package.package_run(record["run_id"], **ctx)
    before = delivered(result["path"])
    ctx["registry"] = sources({"intl-wdi": "derived_only"})
    with pytest.raises(LicenseGateError) as exc:
        package.package_run(record["run_id"], **ctx)
    assert exc.value.code == "license_changed" and delivered(result["path"]) == before
    ctx["registry"] = sources({"intl-wdi": "raw_ok"})
    assert package.package_run(record["run_id"], **ctx) == result
    audit = tmp_path / "eltiro/runs" / (record["run_id"] + ".package-audit.json")
    assert json.loads(audit.read_bytes())["status"] == "allowed"
    ctx["consumers"] = {"example-consumer": consumer(accept_outflow=["derived_only"])}
    with pytest.raises(LicenseGateError, match="consumer_outflow_forbidden"):
        package.package_run(record["run_id"], **ctx)


def test_failed_new_delivery_preserves_previous_package_even_after_rename(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    first = write_run(tmp_path)
    first_result = package.package_run(first["run_id"], **ctx)
    before = delivered(tmp_path / "consumer/outbox")
    second = write_run(tmp_path, values=("25",))
    real_rename = package.os.rename
    real_fsync = package.os.fsync
    published = False
    def rename(*args, **kwargs):
        nonlocal published
        result = real_rename(*args, **kwargs)
        published = True
        return result
    def fsync(descriptor):
        if published:
            raise OSError("synthetic post-rename directory fsync failure")
        return real_fsync(descriptor)
    monkeypatch.setattr(package.os, "rename", rename)
    monkeypatch.setattr(package.os, "fsync", fsync)
    with pytest.raises(OSError):
        package.package_run(second["run_id"], **ctx)
    assert delivered(tmp_path / "consumer/outbox") == before
    assert sorted(p.name for p in first_result["path"].parent.iterdir()) == [first["run_id"]]


def test_mixed_rows_remove_only_restricted_text_and_profile_copies_cannot_grant(tmp_path):
    ctx = context(tmp_path)
    ctx["registry"] = sources({"intl-wdi": "raw_ok", "intl-imf-weo": "derived_only"})
    ctx["consumers"] = {"example-consumer": consumer(("intl-wdi", "intl-imf-weo"), accept_outflow=["raw_ok", "derived_only"])}
    record = write_run(tmp_path, values=("1", "2"))
    run_path = tmp_path / "eltiro/runs" / record["run_id"]
    file = run_path / "citations.json"
    citations = json.loads(file.read_bytes())
    citations[1]["citation"]["inputs"][0]["source_id"] = "intl-imf-weo"
    file.write_text(json.dumps(citations))
    # Deliberately corrupt audit/cache copies; neither is an authority input.
    (run_path / "profile-cache.json").write_text(json.dumps({"source_id": "intl-imf-weo", "outflow": "raw_ok"}))
    table = pq.read_table(run_path / "data.parquet")
    rows = table.to_pylist()
    rows[0]["note"] = "SYNTHETIC_RAW_NOTE"
    rows[0]["series"] = "SYNTHETIC_RAW_INDICATOR"
    rows[0]["unit"] = "USD"
    rows[1]["source"] = "intl-imf-weo"
    import pyarrow as pa
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), run_path / "data.parquet")
    result = package.package_run(record["run_id"], **ctx)
    output = pq.read_table(result["path"] / "data.parquet").to_pylist()
    assert output[0]["note"] == "SYNTHETIC_RAW_NOTE" and output[1]["note"] is None
    assert all(MARKER.encode() not in p.read_bytes() for p in result["path"].iterdir())
    manifest = OutputManifest.model_validate_json((result["path"] / "manifest.json").read_bytes())
    assert manifest.license_summary.by_source["intl-imf-weo"] == "derived_only"


def test_denial_before_first_delivery_does_not_create_outbox(tmp_path):
    ctx = context(tmp_path, "no_outflow")
    record = write_run(tmp_path)
    with pytest.raises(LicenseGateError, match="no_outflow"):
        package.package_run(record["run_id"], **ctx)
    assert not (tmp_path / "consumer").exists()
