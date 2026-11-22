"""Offline historical-release import: preview by default, leased generation publish on --write."""

import hashlib
import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud, wiring
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeRawStore, FakeRegistry, FakeStateObjectStore
from tests.crawlers.test_imf_sdmx import GRC_SPEC
from tests.integration.test_imf_weo_e2e import close, crawl_and_parse, routes_for

SID = "intl-imf-weo"
ARCHIVE = Path(__file__).parents[1] / "tests/fixtures/imf_weo_archive"
RELEASES = [  # release_id and released_at come only from the manifest
    ("weo_2009_apr_GRC_excerpt.tsv", "WEO-2009-APR", "2009-04-22", "GGB_NGDP", "-4.509"),
    ("weo_2009_oct_GRC_excerpt.tsv", "WEO-2009-OCT", "2009-10-01", "GGB_NGDP", "-6.398"),
    ("weo_2010_apr_GRC_excerpt.tsv", "WEO-2010-APR", "2010-04-21", "GGXCNL_NGDP", "-12.866"),
    ("weo_2010_oct_GRC_excerpt.tsv", "WEO-2010-OCT", "2010-10-06", "GGXCNL_NGDP", "-13.584"),
]


class StoreRaw(FakeRawStore):
    """Stands in for the remote raw store: uploads land in the state store's raw/ keys."""

    def __init__(self, store):
        super().__init__()
        self.store = store

    def put(self, data):
        sha = super().put(data)
        self.store.put(f"raw/{sha[:2]}/{sha}", self.blobs[sha])
        return sha


@pytest.fixture
def world(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("tests must never access the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", "synthetic-bucket")
    monkeypatch.setenv("R2_ENDPOINT", "https://synthetic-endpoint.invalid")
    registry = FakeRegistry([GRC_SPEC])
    original = wiring.build
    store = FakeStateObjectStore()
    seed_root = tmp_path / "seed"
    deps = original(seed_root, {"registry": registry, "vectors": None})
    crawl_and_parse(deps, GRC_SPEC, routes_for(tmp_path))
    StateManager(store, seed_root).push()
    close(deps)

    def build(root, overrides=None):
        return original(root, {"registry": registry, "vectors": None, "raw": StoreRaw(store), **(overrides or {})})

    monkeypatch.setattr(wiring, "build", build)
    monkeypatch.setattr(wiring, "build_state", lambda root: StateManager(store, root))
    files = tmp_path / "files"
    files.mkdir()
    entries = []
    for name, release_id, released_at, _, _ in RELEASES:
        shutil.copy(ARCHIVE / name, files / name)
        data = (files / name).read_bytes()
        entries.append({"source": SID, "release_id": release_id, "released_at": released_at, "file": name,
                        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(entries))
    return SimpleNamespace(store=store, tmp=tmp_path, files=files, manifest=manifest, entries=entries,
                           original=original, registry=registry)


def current_generation(store):
    return json.loads(store.get("current.json")[0])["generation"]


def run(world, name, **kw):
    return cloud.import_release(manifest=world.manifest, files_dir=world.files, work_dir=world.tmp / name, **kw)


def test_dry_run_reports_each_release_and_writes_nothing(world, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run tried to acquire a lease or probe writes")

    monkeypatch.setattr(cloud.LeaseManager, "acquire", forbidden)
    monkeypatch.setattr(cloud, "probe_conditional_writes", forbidden)
    before = dict(world.store._objects)
    response = CliRunner().invoke(cli.app, [
        "cloud", "import-release", "--manifest", str(world.manifest), "--files-dir", str(world.files),
        "--work-dir", str(world.tmp / "preview")])
    assert response.exit_code == 0, response.output
    result = json.loads(response.output)
    assert result["write"] is False and "generation" not in result
    assert [(r["release_id"], r["status"]) for r in result["releases"]] == [
        (rid, "previewed") for _, rid, _, _, _ in RELEASES]
    assert all(r["observations"] > 0 and isinstance(r["warnings"], list) for r in result["releases"])
    assert world.store._objects == before


def progress_events(captured):
    return [json.loads(line) for line in captured.err.splitlines() if line.startswith("{")]


@pytest.mark.parametrize("write", [False, True])
def test_progress_goes_to_stderr_and_stdout_stays_one_json(world, capfd, write):
    args = ["cloud", "import-release", "--manifest", str(world.manifest), "--files-dir", str(world.files),
            "--work-dir", str(world.tmp / f"progress-{write}")] + (["--write"] if write else [])
    response = CliRunner().invoke(cli.app, args)
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)["write"] is write  # stdout: only the final result
    events = progress_events(capfd.readouterr())
    names = [e["event"] for e in events]
    ids = [r[1] for r in RELEASES]
    assert names[:3] == ["manifest_checked", "restore_start", "restore_end"]
    assert [(e["event"], e["release_id"]) for e in events if e["event"].startswith("import_")] == [
        (kind, rid) for rid in ids for kind in ("import_start", "import_end")]
    assert all(e["observations"] > 0 for e in events if e["event"] == "import_end")
    assert names[-2:] == (["publish_start", "publish_end"] if write else ["import_start", "import_end"])
    text = json.dumps(events)
    assert "synthetic" not in text and "http" not in text  # stages, ids, counts and time only
    assert all(isinstance(e["elapsed_s"], float) for e in events)


def test_remote_restore_uses_parallel_transfers(world, monkeypatch):
    seen = []
    original = StateManager.restore

    def restore(self, *args, **kwargs):
        restored = original(self, *args, **kwargs)
        seen.append(self.transfer_workers)  # recorded only after a successful restore
        return restored

    monkeypatch.setattr(cloud, "S3StateObjectStore", FakeStateObjectStore)
    monkeypatch.setattr(StateManager, "restore", restore)
    run(world, "parallel")  # the fake store lacks S3 usage listing, so only the restore is checked
    assert seen == [8]


def test_write_publishes_history_and_current_keeps_its_value(world):
    result = run(world, "write", write=True)
    assert "exit_status" not in result, result
    assert result["generation"] == 2 and current_generation(world.store) == 2
    assert [r["status"] for r in result["releases"]] == ["imported"] * 4
    assert json.loads(world.store.get("write-lease.json")[0]).keys() == {"released_at"}
    root = world.tmp / "check"
    wiring._ReleaseStateManager(world.store, root).restore(root)
    deps = world.original(root, {"registry": world.registry, "vectors": None})
    try:
        token = deps.observations.pin_generation()
        table = "WEO:GRC.GGXCNL_NGDP.A"
        for _, release_id, released_at, code, value in RELEASES:
            sql = ("SELECT release_id, released_at, value FROM observations WHERE area_code='GRC' "
                   f"AND indicator_code='{code}' AND time_label='2009' AND unit='PT'")
            answer = deps.observations.query_readonly(sql, generation_token=token, as_of_release=release_id)
            assert answer.rows == [(release_id, date.fromisoformat(released_at), Decimal(value))]
        current = deps.observations.query_readonly(
            f"SELECT release_id FROM observations WHERE table_key='{table}' AND time_label='2009'").rows
        assert current == [("WEO-2026-OCT",)]
    finally:
        close(deps)


@pytest.mark.parametrize("damage", ["sha", "size", "missing", "bad_manifest"])
def test_bad_file_rejects_whole_batch_before_touching_storage(world, damage):
    entries = [dict(e) for e in world.entries]
    if damage == "sha":
        entries[3]["sha256"] = "0" * 64
    elif damage == "size":
        entries[2]["bytes"] += 1
    elif damage == "missing":
        (world.files / entries[1]["file"]).unlink()
    else:
        entries[0]["released_at"] = "2009-4-22"
    world.manifest.write_text(json.dumps(entries))
    before = dict(world.store._objects)
    result = run(world, "bad", write=True)
    expected = {"sha": "file_mismatch", "size": "file_mismatch", "missing": "file_missing",
                "bad_manifest": "manifest_invalid"}[damage]
    assert result["exit_status"] == 1 and result["reason"] == expected
    assert world.store._objects == before


def test_generation_change_between_preview_and_lease_is_rejected(world, monkeypatch):
    original = StateManager.list

    def history(manager):
        result = original(manager)
        found = world.store.get("write-lease.json")
        if found and "token" in json.loads(found[0]):
            return [result[0].model_copy(update={"generation": 2})]
        return result

    monkeypatch.setattr(StateManager, "list", history)
    result = run(world, "raced", write=True)
    assert result["exit_status"] == 1 and result["reason"] == "generation_changed"
    assert current_generation(world.store) == 1
    assert all(r["status"] != "imported" for r in result["releases"])


def test_orphan_generation_is_rejected(world):
    world.store.put("generations/2.json", b"synthetic orphan")
    result = run(world, "orphan", write=True)
    assert result["exit_status"] == 1 and result["reason"] == "orphan_generation"
    assert current_generation(world.store) == 1


def test_capacity_limit_stops_before_lease(world, monkeypatch):
    monkeypatch.setattr(cloud, "_project_storage_bytes", lambda store: 9_000_000_000)
    before = dict(world.store._objects)
    result = run(world, "full", write=True)
    assert result["reason"] == "storage_limit_exceeded" and world.store._objects == before


def test_failed_release_publishes_nothing(world, monkeypatch):
    original = wiring.import_observation_release
    calls = []

    def flaky(deps, source, file, release_id, released_at):
        calls.append(release_id)
        if len(calls) == 3:
            raise ValueError("synthetic parser text with secret-token")
        return original(deps, source, file, release_id, released_at)

    monkeypatch.setattr(wiring, "import_observation_release", flaky)
    result = run(world, "failing", write=True)
    assert result["exit_status"] == 1 and result["reason"] == "import_failed"
    assert result["failed_release"] == "WEO-2010-APR" and "secret-token" not in json.dumps(result)
    assert current_generation(world.store) == 1
    assert {r["status"] for r in result["releases"]} == {"not_published"}


def test_repeat_skips_existing_releases(world):
    first = run(world, "first", write=True)
    assert first["generation"] == 2
    second = run(world, "second", write=True)
    assert "exit_status" not in second, second
    assert {r["status"] for r in second["releases"]} == {"skipped_existing"}
    assert second["generation"] == 2 and current_generation(world.store) == 2
    preview = run(world, "third")
    assert {r["status"] for r in preview["releases"]} == {"skipped_existing"}


def test_output_has_no_endpoint_bucket_or_secret(world, monkeypatch):
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "synthetic-secret-value")
    outputs = [json.dumps(run(world, "p1")), json.dumps(run(world, "p2", write=True))]
    for text in outputs:
        for forbidden in ("synthetic-endpoint", "synthetic-bucket", "synthetic-secret-value"):
            assert forbidden not in text


OLD_NAME = "Old subject descriptor"


def seed_with_old_names(world, monkeypatch):
    """Publish all four archive releases with the pre-316 indicator text, as R2 holds them."""
    from datumoj.rikolto.store.observations import DuckDBObservationStore

    original = DuckDBObservationStore.import_release

    def renamed(self, source, table, observations, order):
        return original(self, source, table, [o.model_copy(update={"indicator": OLD_NAME}) for o in observations], order)

    with monkeypatch.context() as patch:
        patch.setattr(DuckDBObservationStore, "import_release", renamed)
        seeded = run(world, "old-names", write=True)
        assert seeded.get("generation") == 2, seeded.get("reason")


def stored_rows(world, name):
    root = world.tmp / name
    wiring._ReleaseStateManager(world.store, root).restore(root)
    deps = world.original(root, {"registry": world.registry, "vectors": None})
    try:
        cloud._stored_release_ids(deps, SID)  # installs the restored history segments
        return [row for path in sorted((deps.observations._parquet / SID).glob("*.parquet"))
                for row in deps.observations._stored_rows(path)]
    finally:
        close(deps)


def test_replace_swaps_only_the_named_release(world, monkeypatch):
    seed_with_old_names(world, monkeypatch)
    before = stored_rows(world, "before")
    apr = [r for r in before if r["release_id"] == "WEO-2010-APR"]
    assert apr and {r["indicator"] for r in apr} == {OLD_NAME}
    assert {r["release_id"] for r in before} >= {"WEO-2009-APR", "WEO-2010-OCT", "WEO-2026-OCT"}

    plain = run(world, "plain", write=True)  # no --replace: exactly the old behaviour
    assert {r["status"] for r in plain["releases"]} == {"skipped_existing"} and plain["generation"] == 2

    result = run(world, "replace", write=True, replace_releases=["WEO-2010-APR"])
    assert "exit_status" not in result, result
    assert result["generation"] == 3 and current_generation(world.store) == 3
    assert {r["release_id"]: r["status"] for r in result["releases"]} == {
        "WEO-2009-APR": "skipped_existing", "WEO-2009-OCT": "skipped_existing",
        "WEO-2010-APR": "replaced", "WEO-2010-OCT": "skipped_existing"}
    report = next(r for r in result["releases"] if r["release_id"] == "WEO-2010-APR")
    assert (report["replaced_current_rows"], report["replaced_history_rows"]) == (0, len(apr))  # GRC only: history
    assert report["observations"] > 0

    after = stored_rows(world, "after")
    keys = [(r["obs_id"], r["release_id"]) for r in after]
    assert len(keys) == len(set(keys)) == len(before)  # one row per (obs_id, release_id), none added or lost
    assert OLD_NAME not in {r["indicator"] for r in after if r["release_id"] == "WEO-2010-APR"}
    untouched = lambda rows: sorted((r["obs_id"], r["release_id"], r["indicator"]) for r in rows
                                    if r["release_id"] != "WEO-2010-APR")
    assert untouched(after) == untouched(before)  # other releases and the current rows did not change


def test_replace_preview_reports_counts_and_uploads_nothing(world, monkeypatch):
    seed_with_old_names(world, monkeypatch)
    count = sum(r["release_id"] == "WEO-2010-APR" for r in stored_rows(world, "before"))

    def forbidden(*args, **kwargs):
        pytest.fail("preview tried to acquire a lease or probe writes")

    monkeypatch.setattr(cloud.LeaseManager, "acquire", forbidden)
    objects = dict(world.store._objects)
    result = run(world, "preview", replace_releases=["WEO-2010-APR"])
    assert "exit_status" not in result and "generation" not in result
    statuses = {r["release_id"]: r["status"] for r in result["releases"]}
    assert statuses["WEO-2010-APR"] == "would_replace" and statuses["WEO-2010-OCT"] == "skipped_existing"
    report = next(r for r in result["releases"] if r["release_id"] == "WEO-2010-APR")
    assert (report["replaced_current_rows"], report["replaced_history_rows"]) == (0, count)
    assert report["observations"] > 0
    assert world.store._objects == objects and current_generation(world.store) == 2
    text = json.dumps(result)
    assert all(word not in text for word in ("synthetic-endpoint", "synthetic-bucket"))


def test_replace_unknown_release_is_refused_without_touching_storage(world, monkeypatch):
    monkeypatch.setattr(wiring, "build_state", lambda root: pytest.fail("storage was opened"))
    result = run(world, "unknown", write=True, replace_releases=["WEO-1999-APR"])
    assert result["exit_status"] == 1 and result["reason"] == "manifest_invalid"


def test_replace_with_other_identities_is_refused_and_writes_nothing(world):
    """The archive file is not the API release's observation set: refused before any lease or write."""
    shutil.copy(world.files / RELEASES_FILE, world.files / "current.tsv")
    data = (world.files / "current.tsv").read_bytes()
    entries = [*world.entries, {"source": SID, "release_id": "WEO-2026-OCT", "released_at": "2026-10-01",
                                "file": "current.tsv", "sha256": hashlib.sha256(data).hexdigest(),
                                "bytes": len(data)}]
    world.manifest.write_text(json.dumps(entries))
    objects = dict(world.store._objects)
    for write in (False, True):
        result = run(world, f"current-{write}", write=write, replace_releases=["WEO-2026-OCT"])
        assert result["exit_status"] == 1 and result["reason"] == "replace_identity_mismatch"
        assert world.store._objects == objects
    assert current_generation(world.store) == 1


RELEASES_FILE = "weo_2010_apr_GRC_excerpt.tsv"
API_NAME = "Net lending (+) / net borrowing (-), General government, Percent of GDP"


def widen_to_japan(world):
    """Make the 2010 files full-file-like: a second country with the same series, in tables the API never fetched.

    Synthetic: the real mixed excerpts hold no other country's GGXCNL_NGDP, so a Japan row is cloned from the
    Greek one (raw cp1252 bytes, only code, ISO and country name changed) and the plan covers both countries.
    """
    series = [{**GRC_SPEC.scope["series"][0], "query": "GRC+JPN.GGXCNL_NGDP.A"}]
    world.registry._specs[SID] = GRC_SPEC.model_copy(update={"scope": {**GRC_SPEC.scope, "series": series}})
    for entry in world.entries:
        if not entry["release_id"].startswith("WEO-2010"):
            continue
        path = world.files / entry["file"]
        lines = path.read_bytes().split(b"\r\n")
        at = next(i for i, line in enumerate(lines) if line.startswith(b"174\tGRC\tGGXCNL_NGDP\t"))
        japan = lines[at].replace(b"174\tGRC", b"158\tJPN", 1).replace(b"Greece", b"Japan", 1)
        data = b"\r\n".join([*lines[:at + 1], japan, *lines[at + 1:]])
        path.write_bytes(data)
        entry.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    world.manifest.write_text(json.dumps(world.entries))


def stored_tables(world, name):
    """Per table: every stored row (history scan), which of them are in the current file, and the watermark."""
    import pyarrow.parquet as pq

    root = world.tmp / name
    wiring._ReleaseStateManager(world.store, root).restore(root)
    deps = world.original(root, {"registry": world.registry, "vectors": None})
    try:
        cloud._stored_release_ids(deps, SID)  # installs the restored history segments
        tables = {}
        store = deps.observations
        for path in sorted((store._parquet / SID).glob("*.parquet")):
            rows = sorted(store._stored_rows(path), key=lambda r: (r["release_id"], r["obs_id"]))
            current = {(r["obs_id"], r["release_id"]) for r in pq.read_table(path).to_pylist()}
            tables[rows[0]["table_key"]] = {
                "rows": [{**r, "in_current_file": (r["obs_id"], r["release_id"]) in current} for r in rows],
                "order": store.table_order(SID, rows[0]["table_key"])}
        return tables
    finally:
        close(deps)


def test_replace_swaps_current_and_history_rows_across_country_tables(world, monkeypatch):
    widen_to_japan(world)
    seed_with_old_names(world, monkeypatch)
    before = stored_tables(world, "before")
    grc, jpn = before["WEO:GRC.GGXCNL_NGDP.A"], before["WEO:JPN.GGXCNL_NGDP.A"]
    flags = lambda table, release: {r["in_current_file"] for r in table["rows"] if r["release_id"] == release}
    assert flags(grc, "WEO-2010-APR") == flags(grc, "WEO-2010-OCT") == {False}  # the API row is current in GRC
    assert flags(jpn, "WEO-2010-OCT") == {True} and flags(jpn, "WEO-2010-APR") == {False}  # Japan: no API row
    counts = {release: {"current": sum(r["in_current_file"] for t in (grc, jpn) for r in t["rows"]
                                       if r["release_id"] == release),
                        "history": sum(not r["in_current_file"] for t in (grc, jpn) for r in t["rows"]
                                       if r["release_id"] == release)}
              for release in ("WEO-2010-APR", "WEO-2010-OCT")}
    assert counts["WEO-2010-OCT"]["current"] > 0 and counts["WEO-2010-APR"]["current"] == 0
    assert {r["indicator"] for t in (grc, jpn) for r in t["rows"] if r["release_id"].startswith("WEO-2010")} == {OLD_NAME}

    named = ["WEO-2010-APR", "WEO-2010-OCT"]
    objects = dict(world.store._objects)
    preview = run(world, "preview", replace_releases=named)  # the rehearsal that failed on 2026-11-23
    assert "exit_status" not in preview and "generation" not in preview, preview
    assert world.store._objects == objects and current_generation(world.store) == 2
    result = run(world, "replace", write=True, replace_releases=named)
    assert "exit_status" not in result, result
    assert result["generation"] == 3 and current_generation(world.store) == 3
    for run_result, status in ((preview, "would_replace"), (result, "replaced")):
        assert {r["release_id"]: r["status"] for r in run_result["releases"]} == {
            "WEO-2009-APR": "skipped_existing", "WEO-2009-OCT": "skipped_existing",
            "WEO-2010-APR": status, "WEO-2010-OCT": status}
        for report in run_result["releases"]:
            if report["status"] == status:
                expected = counts[report["release_id"]]
                assert (report["replaced_current_rows"], report["replaced_history_rows"]) == (
                    expected["current"], expected["history"])

    after = stored_tables(world, "after")
    assert after.keys() == before.keys()
    keep = ("obs_id", "release_id", "_current", "_order_at", "_order_seq", "in_current_file")
    for key, table in before.items():
        assert after[key]["order"] == table["order"]  # watermark
        assert len(after[key]["rows"]) == len(table["rows"])  # no duplicate, no leftover
        for old, now in zip(table["rows"], after[key]["rows"], strict=True):
            assert {k: now[k] for k in keep} == {k: old[k] for k in keep}  # same membership and sort key
            if old["release_id"].startswith("WEO-2010"):
                assert old["indicator"] == OLD_NAME and now["indicator"] == API_NAME
            else:
                assert now == old
    for release in ("WEO-2010-APR", "WEO-2010-OCT"):
        assert {r["indicator"] for t in after.values() for r in t["rows"] if r["release_id"] == release} == {API_NAME}

    root = world.tmp / "answers"
    wiring._ReleaseStateManager(world.store, root).restore(root)
    deps = world.original(root, {"registry": world.registry, "vectors": None})
    try:
        token = deps.observations.pin_generation()
        sql = ("SELECT release_id, indicator, value FROM observations WHERE area_code='GRC' "
               "AND indicator_code='GGXCNL_NGDP' AND time_label='2009' AND unit='PT'")
        for release, value in (("WEO-2010-APR", "-12.866"), ("WEO-2010-OCT", "-13.584")):
            answer = deps.observations.query_readonly(sql, generation_token=token, as_of_release=release)
            assert answer.rows == [(release, API_NAME, Decimal(value))]
        current = deps.observations.query_readonly(  # the current file itself, not only the history segments
            "SELECT release_id, indicator FROM observations WHERE table_key='WEO:JPN.GGXCNL_NGDP.A' "
            "AND time_label='2009'").rows
        assert current == [("WEO-2010-OCT", API_NAME)]
    finally:
        close(deps)

    plain = run(world, "plain", write=True)  # without --replace nothing is touched
    assert {r["status"] for r in plain["releases"]} == {"skipped_existing"} and plain["generation"] == 3
