"""Offline tests for scripts/eltiro_cloud.py: recorded samples (the m1 construction) in a retained generation 7.

The read-only restore itself is cloud_qa's (tests/test_cloud_qa.py covers it against a fake store); here it is replaced by
a function that returns the generation of a pre-built data directory, so what runs for real is: intake, profiles,
deterministic matching, confirmation, execution, packaging behind the license gate and the gap report.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
from decimal import Decimal
from pathlib import Path

import boto3
import pyarrow.parquet as pq
import pytest
import yaml

from datumoj.eltiro.intake import IntakeService
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.models import ConsumersConfig
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.rikolto import wiring
from datumoj.rikolto.core.models import GenerationToken
from datumoj.rikolto.core.registry import load_registry
from tests.integration.test_wisteria_m1 import CONTROL_ID, IMF, REGISTERED, _control_demand, _parsed, _retain, _write

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
# The fake remote store and the published synthetic generation of the QA tests (moto S3), used for the real restore.
QA_SPEC = importlib.util.spec_from_file_location("eltiro_cloud_qa_tests", ROOT / "tests/test_cloud_qa.py")
qa_tests = importlib.util.module_from_spec(QA_SPEC)
QA_SPEC.loader.exec_module(qa_tests)
remote, published_qa = qa_tests.remote, qa_tests.published_qa
CONSUMERS = ROOT / "eval/eltiro/consumers-cloud.yaml"
SAMPLE = ROOT / "eval/eltiro/bindings/sample-three-gdp-for-tests-only.yaml"
GDP = "three-gdp-reconciliation"
GENERATION = 7


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("eltiro_cloud")
    monkeypatch.setattr(module, "restore_latest", lambda root: GENERATION)
    return module


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    root = tmp_path_factory.mktemp("eltiro-cloud-base").resolve() / "data"
    root.mkdir()
    registry = load_registry(ROOT / "sources")
    specs = {sid: registry.get(sid) for sid in REGISTERED}
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATUMOJ_RAW_BACKEND", "local")
        patch.setenv("DATUMOJ_VECTORS", "off")
        parsed, _ = _parsed(specs, codelists=True)
        _write(root, parsed)
        _retain(root, GENERATION)
    return root


@pytest.fixture
def data(base, tmp_path):
    """A fresh copy of the restored data directory (what a workflow run starts from)."""
    target = tmp_path / "data"
    shutil.copytree(base, target, symlinks=True)
    return target.resolve()


def go(cloud, data, tmp_path, demands=GDP, bindings=None, **dirs):
    return cloud.run(data, demands, None if bindings is None else str(bindings), tmp_path / "out", **dirs)


def load(tmp_path):
    return json.loads((tmp_path / "out/summary.json").read_text(encoding="utf-8"))


def plain(value):
    """The digits of a Decimal without exponent or padding zeros after the point: '5189196607000.000' -> '5189196607000'."""
    return format(value.normalize(), "f")


def all_values(data):
    """Every non-null value any run of this data directory produced, as plain strings (>= 7 digits)."""
    found = set()
    for path in (data / "eltiro/runs").glob("run-*/data.parquet"):
        for row in pq.read_table(path).to_pylist():
            for name in ("value", "spread_range"):
                if row[name] is not None and len(plain(row[name]).lstrip("-")) >= 7:
                    found.add(plain(row[name]))
    return found


def assert_public(text, data, tmp_path, *, runs_with_values):
    values = all_values(data)
    # The scan below is not vacuous when the runs hold values (the confirmed run of #002 does; an unconfirmed run is empty).
    assert bool(values) is runs_with_values
    assert not [value for value in values if value in text]
    assert str(tmp_path) not in text and str(data) not in text and str(ROOT) not in text


def test_first_trigger_lists_candidates_and_runs_unconfirmed(cloud, data, tmp_path, capfd):
    result = go(cloud, data, tmp_path)
    captured = capfd.readouterr()
    assert captured.err == ""
    assert result["exit_status"] == 0 and result["error_code"] is None and result["generation"] == GENERATION
    assert (result["demands"], result["accepted"], result["runs"], result["confirmed"], result["packages"]) == (1, 1, 1, 0, 0)
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["summary.json", "summary.md"]
    summary = load(tmp_path)
    assert GenerationToken.parse(summary["generation_token"]).generation == GENERATION == summary["generation"]
    assert summary["mode"] == "candidates_only" and summary["models"]["calls"] == 0
    (demand,) = summary["demands"]
    assert demand["intake"] == {"conclusion": "accepted", "reason_code": "accepted"}
    assert demand["unconfirmed_run"]["row_count"] == 0 and "confirmed_run" not in demand and "license_gate" not in demand
    indicators = {i["key"]: i for i in demand["indicators"]}
    assert set(indicators) == {"gdp_usd", "gdp_lcu"}
    candidates = [c for i in indicators.values() for c in i["candidates"]]
    assert candidates and {c["confirmation"] for c in candidates} == {"pending"}
    assert {c["tier"] for c in candidates} == {"deterministic"}
    wdi = {c["indicator_code"]: c for c in indicators["gdp_usd"]["candidates"] if c["source_id"] == "intl-wdi"}
    # Scale and its basis come from the parser's declaration only: CN has recorded metadata, CD has none.
    assert wdi["NY.GDP.MKTP.CN"]["scale"]["multiplier"] == 1.0 and wdi["NY.GDP.MKTP.CN"]["scale"]["evidence"]
    assert wdi["NY.GDP.MKTP.CD"]["scale"] == {"multiplier": None, "evidence": None}
    assert {"source_id", "indicator_code", "fixed_dimensions", "unit", "reason", "confidence"} <= set(candidates[0])
    assert {(g["symbol"], g["reason"]) for g in demand["unconfirmed_run"]["gaps"]} >= {
        ("gdp_usd", "pending_confirmation"), ("gdp_usd", "unbound_indicator")}
    assert "pending_confirmation" in indicators["gdp_usd"]["gap_report"]["gaps"]
    assert indicators["gdp_usd"]["gap_report"]["bound_sources"] == []
    text = json.dumps(summary, ensure_ascii=False) + (tmp_path / "out/summary.md").read_text(encoding="utf-8")
    assert_public(text + captured.out, data, tmp_path, runs_with_values=False)


def test_confirmation_list_confirms_runs_and_the_gate_refuses_without_output(cloud, data, tmp_path):
    result = go(cloud, data, tmp_path, bindings=SAMPLE)
    assert result["exit_status"] == 0 and result["error_code"] is None
    assert result["runs"] == 2 and result["refused"] == 1 and result["packages"] == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["summary.json", "summary.md"]
    summary = load(tmp_path)
    (demand,) = summary["demands"]
    entries = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))["bindings"]
    assert [b["candidates_hit"] >= 1 for b in demand["bindings"]] == [True] * len(entries)
    candidates = [c for i in demand["indicators"] for c in i["candidates"]]
    wanted = {(e["indicator"], e["source_id"], e["indicator_code"]) for e in entries}
    confirmed = [c for c in candidates if c["confirmation"] == "confirmed"]
    assert confirmed and result["confirmed"] == len(confirmed)
    assert all((c["indicator"], c["source_id"], c["indicator_code"]) in wanted for c in confirmed)
    assert {c["confirmation"] for c in candidates if c not in confirmed} == {"rejected"}
    run = demand["confirmed_run"]
    assert run["row_count"] > 0 and {s["source"] for s in run["series"] if s["kind"] == "indicator"} == {
        IMF, "intl-oecd", "intl-wdi"}
    assert all(s["non_null"] <= s["rows"] for s in run["series"])
    gate = demand["license_gate"]
    # #002's lineage has two no_outflow sources: the whole package is refused, nothing is copied out.
    assert gate["status"] == "refused" and gate["code"] == "no_outflow" and "intl-oecd" in gate["sources"]
    assert gate["lineage"] == {IMF: "raw_ok", "intl-oecd": "no_outflow", "intl-wdi": "no_outflow"}
    assert "package" not in gate
    assert not (data / "consumer/outbox" / GDP).exists()
    usd = {i["key"]: i for i in demand["indicators"]}["gdp_usd"]["gap_report"]
    assert usd["bound_sources"] == [IMF, "intl-oecd", "intl-wdi"]
    text = json.dumps(summary, ensure_ascii=False) + (tmp_path / "out/summary.md").read_text(encoding="utf-8")
    assert "5189196607000" in all_values(data)
    assert_public(text, data, tmp_path, runs_with_values=True)


def test_only_raw_ok_sources_deliver_the_package_with_the_artifact(cloud, data, tmp_path):
    demands, bindings = tmp_path / "demands", tmp_path / "bindings"
    demands.mkdir()
    bindings.mkdir()
    (demands / f"{CONTROL_ID}.yaml").write_text(_control_demand(), encoding="utf-8")
    entry = {"demand": CONTROL_ID, "indicator": "gdp_usd", "source_id": IMF, "indicator_code": "NGDPD", "dimensions": {}}
    (bindings / "control.yaml").write_text(yaml.safe_dump({"bindings": [entry]}), encoding="utf-8")
    result = go(cloud, data, tmp_path, CONTROL_ID, bindings / "control.yaml", demands_dir=demands, bindings_dir=bindings)
    assert result["exit_status"] == 0 and result["packages"] == 1 and result["refused"] == 0
    summary = load(tmp_path)
    gate = summary["demands"][0]["license_gate"]
    assert gate["status"] == "passed" and gate["uploaded"] is True and gate["package"] == f"packages/{CONTROL_ID}"
    assert gate["lineage"] == {IMF: "raw_ok"} and gate["row_count"] == 10
    package = tmp_path / "out/packages" / CONTROL_ID
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["models"] == [] and manifest["license_summary"]["by_source"] == {IMF: "raw_ok"}
    provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
    assert len(provenance) == 10 and {p["source"] for p in provenance} == {IMF}
    assert Decimal(next(p["value"] for p in provenance if p["country"] == "JPN" and p["period"].startswith("2020"))) == Decimal(5189196607000)
    assert (package / "data.parquet").is_file() and (package / "data.csv").is_file()
    # The values are in the package (allowed: raw_ok) but never in the summary.
    text = json.dumps(summary, ensure_ascii=False) + (tmp_path / "out/summary.md").read_text(encoding="utf-8")
    assert not [p["value"] for p in provenance if len(plain(Decimal(p["value"])).lstrip("-")) >= 7
                and plain(Decimal(p["value"])) in text]
    assert str(tmp_path) not in text and str(data) not in text


def test_upload_decision_needs_rows_and_only_raw_ok_sources(cloud):
    assert cloud.upload_decision({IMF: "raw_ok"}, 3) == (True, None, [])
    assert cloud.upload_decision({IMF: "raw_ok"}, 0) == (False, "no_rows", [])
    assert cloud.upload_decision({IMF: "raw_ok", "x": "derived_only", "a": "no_outflow"}, 3) == (
        False, "non_raw_ok_source", ["a", "x"])


def test_unmatched_entry_fails_before_any_run_or_decision(cloud, data, tmp_path):
    bindings = tmp_path / "bindings"
    bindings.mkdir()
    entries = yaml.safe_load(SAMPLE.read_text(encoding="utf-8"))["bindings"]
    # The m1 finding: OECD has no candidate for gdp_lcu, so a list that names one is wrong as a whole.
    entries.append({"demand": GDP, "indicator": "gdp_lcu", "source_id": "intl-oecd", "indicator_code": "B1GQ",
                    "dimensions": {"UNIT_MEASURE": "XDC"}})
    (bindings / "wrong.yaml").write_text(yaml.safe_dump({"bindings": entries}), encoding="utf-8")
    result = go(cloud, data, tmp_path, bindings=bindings / "wrong.yaml", bindings_dir=bindings)
    assert (result["exit_status"], result["error_code"]) == (1, "bindings_unmatched")
    assert result["runs"] == 0 and not list((data / "eltiro").glob("runs/run-*"))
    assert not (tmp_path / "out").exists()
    with SQLiteMatchStore(data) as matches:
        records = matches.for_demand(GDP, 1, GENERATION)
    assert records and {r.confirmation for r in records} == {"pending"}


@pytest.mark.parametrize("value", ["", "A", "a b", "a;b", "a$b", "../x", "a/b", "-x", "a,,b", ",a", "a,", "a,a", "ü",
                                   "a\n", ",".join(f"d{i}" for i in range(11))])
def test_invalid_demand_ids_fail_before_restore(cloud, data, tmp_path, monkeypatch, value):
    def forbidden(root):
        pytest.fail("the restore must not start for invalid arguments")

    monkeypatch.setattr(cloud, "restore_latest", forbidden)
    result = go(cloud, data, tmp_path, value)
    assert (result["exit_status"], result["error_code"]) == (1, "invalid_demands")
    assert not (tmp_path / "out").exists()


def test_non_empty_output_directory_is_refused_before_restore(cloud, data, tmp_path, monkeypatch):
    def forbidden(root):
        pytest.fail("the restore must not start when an old artifact could ride along")

    monkeypatch.setattr(cloud, "restore_latest", forbidden)
    (tmp_path / "out/packages/old").mkdir(parents=True)
    result = go(cloud, data, tmp_path)
    assert (result["exit_status"], result["error_code"]) == (1, "output_not_empty")


def test_demand_must_be_a_file_of_the_repository_demand_directory(cloud, data, tmp_path):
    assert go(cloud, data, tmp_path, "no-such-demand")["error_code"] == "demand_not_found"
    assert go(cloud, data, tmp_path, f"{GDP},no-such-demand")["error_code"] == "demand_not_found"
    demands = tmp_path / "demands"
    demands.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_bytes((ROOT / f"eval/eltiro/demands/{GDP}.yaml").read_bytes())
    (demands / f"{GDP}.yaml").symlink_to(outside)
    assert go(cloud, data, tmp_path, demands_dir=demands)["error_code"] == "demand_not_found"
    assert not (data / "consumer").exists()


def _lists(tmp_path):
    folder = tmp_path / "bindings"
    folder.mkdir()
    good = {"demand": GDP, "indicator": "gdp_usd", "source_id": IMF, "indicator_code": "NGDPD", "dimensions": {}}
    return folder, good


def test_confirmation_list_path_must_be_a_yaml_file_of_the_bindings_directory(cloud, data, tmp_path):
    folder, good = _lists(tmp_path)
    text = yaml.safe_dump({"bindings": [good]})
    (folder / "ok.yaml").write_text(text, encoding="utf-8")
    (folder / "sub").mkdir()
    (folder / "sub/nested.yaml").write_text(text, encoding="utf-8")
    (folder / "list.txt").write_text(text, encoding="utf-8")
    (tmp_path / "outside.yaml").write_text(text, encoding="utf-8")
    (folder / "link.yaml").symlink_to(tmp_path / "outside.yaml")
    for path in (tmp_path / "outside.yaml", folder / "sub/nested.yaml", folder / "list.txt", folder / "link.yaml",
                 folder / "missing.yaml", folder):
        result = go(cloud, data, tmp_path, bindings=path, bindings_dir=folder)
        assert result["error_code"] == "invalid_bindings_path", path
    assert not (tmp_path / "out").exists() and not (data / "consumer").exists()


def _entry(demand=GDP, code="NGDPD", tail="dimensions: {}", anchor=""):
    return (f"bindings:\n- {anchor}{{demand: {demand}, indicator: gdp_usd, source_id: intl-imf-weo, "
            f"indicator_code: {code}" + (f", {tail}" if tail else "") + "}")


@pytest.mark.parametrize("content", [
    "not: [valid", "- a", "bindings: []", "bindings: {}", "bindings: [1]", "extra: 1\nbindings: []",
    _entry(tail=""),  # no dimensions
    _entry(tail="dimensions: {}, extra: 1"),
    _entry(tail="dimensions: {COUNTRY: 1}"),
    _entry(code="null"),
    _entry(demand="other-demand"),
    _entry(anchor="&a ") + "\n- *a",
])
def test_confirmation_list_entries_are_checked_one_by_one(cloud, data, tmp_path, content):
    folder, _ = _lists(tmp_path)
    (folder / "bad.yaml").write_text(content, encoding="utf-8")
    result = go(cloud, data, tmp_path, bindings=folder / "bad.yaml", bindings_dir=folder)
    assert (result["exit_status"], result["error_code"]) == (1, "invalid_bindings")
    assert result["runs"] == 0 and not (tmp_path / "out").exists()


def test_duplicate_entries_are_refused(cloud, data, tmp_path):
    folder, good = _lists(tmp_path)
    (folder / "dup.yaml").write_text(yaml.safe_dump({"bindings": [good, dict(good)]}), encoding="utf-8")
    assert go(cloud, data, tmp_path, bindings=folder / "dup.yaml", bindings_dir=folder)["error_code"] == "invalid_bindings"


def test_rejected_intake_is_a_result_not_a_failure(cloud, data, tmp_path):
    result = go(cloud, data, tmp_path, f"{GDP},japan-monthly-earnings-revisions")
    assert result["exit_status"] == 0 and result["accepted"] == 1
    by_id = {d["demand_id"]: d for d in load(tmp_path)["demands"]}
    assert by_id["japan-monthly-earnings-revisions"]["intake"]["conclusion"] == "rejected"
    assert "indicators" not in by_id["japan-monthly-earnings-revisions"]
    assert by_id[GDP]["intake"]["conclusion"] == "accepted"


def test_restore_failures_report_only_fixed_codes(cloud, data, tmp_path, monkeypatch, capfd):
    def unverified(root):
        raise cloud.Failure("unverified_generation")

    def broken(root):
        raise RuntimeError(f"secret-endpoint {root}")

    monkeypatch.setattr(cloud, "restore_latest", unverified)
    assert go(cloud, data, tmp_path)["error_code"] == "unverified_generation"
    monkeypatch.setattr(cloud, "restore_latest", broken)
    result = go(cloud, data, tmp_path)
    captured = capfd.readouterr()
    assert (result["exit_status"], result["error_code"], result["phase"]) == (1, "restore_failed", "restore")
    assert "secret-endpoint" not in captured.out + captured.err and not (tmp_path / "out").exists()
    assert result["error_type"] == "RuntimeError" and result["error_at"].startswith("scripts/eltiro_cloud.py:")
    assert "secret-endpoint" not in json.dumps(result)


def test_token_of_another_generation_is_refused(cloud, data, tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "restore_latest", lambda root: GENERATION + 1)
    result = go(cloud, data, tmp_path)
    assert (result["exit_status"], result["error_code"]) == (1, "generation_mismatch")
    assert result["runs"] == 0 and not (tmp_path / "out").exists()


def test_command_line_writes_only_phases_counts_and_codes(cloud, data, tmp_path, capfd):
    status = cloud.main([str(data), str(tmp_path / "out"), f"--demands={GDP}"])
    captured = capfd.readouterr()
    assert status == 0 and captured.err == ""
    *phase_lines, last = captured.out.splitlines()
    final = json.loads(last)
    assert set(final) == {"phase", "generation", "error_code", "exit_status", "demands", "accepted", "profiles",
                          "candidates", "confirmed", "runs", "refused", "packages"}
    assert [json.loads(line)["phase"] for line in phase_lines] == ["restore", "intake", "matching", "run", "gaps",
                                                                   "artifacts"]
    for line in phase_lines:
        assert all(type(v) in {int, float, str} for v in json.loads(line).values())
    assert str(tmp_path) not in captured.out and str(data) not in captured.out
    assert cloud.main([str(data), str(tmp_path / "other"), "--demands=BAD"]) == 1
    assert json.loads(capfd.readouterr().out.splitlines()[-1])["error_code"] == "invalid_demands"


def test_consumer_config_matches_the_registry_and_needs_the_model_ceiling_for_the_original_demand(tmp_path):
    config = ConsumersConfig.model_validate(yaml.safe_load(CONSUMERS.read_text(encoding="utf-8")))
    (consumer,) = config.consumers
    registered = sorted(path.stem for path in (ROOT / "sources").glob("*.yaml"))
    assert list(consumer.allowed_sources) == registered == list(consumer.models.authorized_sources)
    assert [o.value for o in consumer.accept_outflow] == ["raw_ok", "derived_only"]
    registry = load_registry(ROOT / "sources")
    demand = (ROOT / f"eval/eltiro/demands/{GDP}.yaml").read_bytes()

    def intake(name, text):
        root = tmp_path / name
        (root / "eltiro").mkdir(parents=True)
        (root / "eltiro/consumers.yaml").write_text(text, encoding="utf-8")
        (root / "consumer/inbox").mkdir(parents=True)
        (root / f"consumer/inbox/{GDP}.yaml").write_bytes(demand)
        with SQLiteDemandRegistry(root / "eltiro/demands.sqlite3") as demands:
            IntakeService(root, demands, registry).scan()
        (receipt,) = (json.loads(p.read_text()) for p in (root / "consumer/outbox").glob("*.json"))
        return receipt["reason_code"]

    # The demand text asks for max_tier medium, allow_cloud and 200 calls: the configuration is the ceiling, never a call.
    assert intake("real", CONSUMERS.read_text(encoding="utf-8")) == "accepted"
    data = yaml.safe_load(CONSUMERS.read_text(encoding="utf-8"))
    data["consumers"][0]["models"].update(max_tier="none", allow_cloud=False, allowed_providers=["none"])
    assert intake("none-only", yaml.safe_dump(data)) == "model_forbidden"


def test_real_restore_is_read_only_and_the_restored_replica_opens(published_qa, remote, cloud, tmp_path, monkeypatch):
    # The published synthetic generation has documents of the synthetic source jp-qa (no observations). Profiles refuse a
    # source the registry does not know, so the registry gets that one extra synthetic entry; everything else is real.
    from datumoj.rikolto.testing.fakes import FakeRegistry
    from datumoj.rikolto.testing.samples import make_spec

    real = load_registry(ROOT / "sources")
    monkeypatch.setattr("datumoj.rikolto.core.registry.load_registry",
                        lambda directory: FakeRegistry([*real.all(), make_spec("jp-qa")]))
    monkeypatch.setattr(cloud, "restore_latest", importlib.import_module("cloud_qa").restore_latest)
    before = {key: remote.get(key) for key in remote.list()}
    operations = []

    def observe(model, **kwargs):
        operations.append(model.name)

    events = boto3.DEFAULT_SESSION.events
    events.register("before-call.s3", observe)
    try:
        result = go(cloud, tmp_path / "replica", tmp_path)
    finally:
        events.unregister("before-call.s3", observe)
    assert (result["exit_status"], result["error_code"], result["phase"]) == (0, None, "complete")
    assert result["generation"] == published_qa.generation and result["accepted"] == 1 and result["runs"] == 1
    assert operations and set(operations) <= {"GetObject", "HeadObject", "ListObjects", "ListObjectsV2"}
    assert {key: remote.get(key) for key in remote.list()} == before
    summary = load(tmp_path)
    assert GenerationToken.parse(summary["generation_token"]).generation == published_qa.generation == summary["generation"]


def test_a_write_during_the_run_fails_it_and_leaves_the_remote_unchanged(published_qa, remote, cloud, tmp_path, monkeypatch):
    def write(root):
        manager = wiring.build_state(tmp_path / "unused")
        try:
            manager.store.put("forbidden", b"x")
        finally:
            manager.store._client.close()

    monkeypatch.setattr(cloud, "restore_latest", write)
    before = remote.list()
    result = go(cloud, tmp_path / "replica", tmp_path)
    assert (result["exit_status"], result["error_code"]) == (1, "restore_failed")
    assert remote.list() == before and remote.get("forbidden") is None and not (tmp_path / "out").exists()


def test_script_never_writes_the_remote_store():
    text = (SCRIPTS / "eltiro_cloud.py").read_text(encoding="utf-8")
    assert "restore_latest" in text and "wiring.build" not in text
    assert not [word for word in ("StateManager", ".push(", ".put(", "lease", "delete_object", "publish(")
                if word in text]
