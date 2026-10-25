"""Cloud rounds against moto and an HTTP source served only on loopback."""

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import boto3
import httpx
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from datumoj import cli, cloud, wiring
from datumoj.core import schedule
from datumoj.core.contracts import FetchResult, PreconditionFailed
from datumoj.core.lease import LeaseError, LeaseManager
from datumoj.core.models import FetchOutcome, PublishState
from datumoj.core.state import StateManager
from datumoj.testing.fakes import FakeCrawler, FakeEmbedder, FakeParser, FakeRegistry
from datumoj.testing.samples import make_spec

ENV = {
    "R2_ACCESS_KEY_ID": "synthetic-cloud-access",
    "R2_SECRET_ACCESS_KEY": "synthetic-cloud-secret",
    "R2_ENDPOINT": "https://s3.amazonaws.com",
    "DATUMOJ_S3_BUCKET": "synthetic-cloud-bucket",
    "DATUMOJ_S3_PREFIX": "synthetic-cloud-prefix",
    "DATUMOJ_S3_REGION": "us-east-1",
    "DATUMOJ_RAW_BACKEND": "s3",
}
BODY = "Synthetic private source body\n\nSecond paragraph".encode()


class LocalFetcher:
    """Minimal test HttpFetcher: all URLs come from pytest-httpserver."""

    def fetch(self, url, etag=None, last_modified=None, **kwargs):
        response = httpx.get(url, trust_env=False)
        ok = response.status_code == 200
        return FetchResult(url=url, fetched_at=datetime.now(UTC), http_status=response.status_code,
                           outcome=FetchOutcome.ok if ok else FetchOutcome.http_error,
                           content=response.content if ok else None, content_type="text/plain",
                           error=None if ok else "synthetic_http_failure")


@pytest.fixture
def world(tmp_path, monkeypatch, httpserver, caplog):
    for name in ("DATUMOJ_VECTORS", "DATUMOJ_VECTOR_LIMIT", "DATUMOJ_S3_ENDPOINT", "DATUMOJ_WRITER_KIND"):
        monkeypatch.delenv(name, raising=False)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    specs = []
    for index, interval in enumerate(("hourly", "daily", "hourly")):
        path = f"/source{index}.txt"
        httpserver.expect_request(path).respond_with_data(BODY, content_type="text/plain")
        specs.append(make_spec(f"jp-cloud{index}", schedule=interval,
                               base_url=httpserver.url_for("/"), allowed_hosts=[httpserver.host],
                               rate_per_sec=10000, scope={"urls": [httpserver.url_for(path)]}))
    clock = [datetime.now(UTC) - timedelta(hours=2)]
    original_build = wiring.build
    original_scheduler = cloud.Scheduler
    components, managers, tokens = [], [], []
    overrides = {"registry": FakeRegistry(specs), "crawlers": {"fake": FakeCrawler},
                 "fetcher": LocalFetcher(), "vectors": None,
                 "parsers": {("fake", "text/plain"): FakeParser}}

    def build(root):
        deps = original_build(root, overrides)
        components.append(deps)
        return deps

    original_state = wiring.build_state

    def build_state(root):
        manager = original_state(root)
        managers.append(manager)
        return manager

    original_acquire = LeaseManager.acquire

    def acquire(self, *args, **kwargs):
        lease = original_acquire(self, *args, **kwargs)
        tokens.append(lease.token)
        return lease

    monkeypatch.setattr(wiring, "build", build)
    monkeypatch.setattr(wiring, "build_state", build_state)
    monkeypatch.setattr(LeaseManager, "acquire", acquire)
    monkeypatch.setattr(cloud, "utcnow", lambda: clock[0])
    monkeypatch.setattr(cloud, "Scheduler", lambda *a, **kw: original_scheduler(*a, now=lambda: clock[0], **kw))
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing",
                              aws_secret_access_key="testing")
        client.create_bucket(Bucket=ENV["DATUMOJ_S3_BUCKET"])
        state = SimpleNamespace(tmp=tmp_path, overrides=overrides, specs=specs, clock=clock,
                                components=components, managers=managers, tokens=tokens)
        yield state
        for manager in managers:
            manager.store._client.close()
        client.close()
    assert all(value not in caplog.text for value in (*ENV.values(), *tokens, BODY.decode()))


def invoke(world, name="work", *options):
    root = world.tmp / name
    response = CliRunner().invoke(cli.app, ["cloud", "run", "--work-dir", str(root), *options])
    assert all(value not in response.output for value in (*ENV.values(), *world.tokens, BODY.decode()))
    ledger = root / "runs.jsonl"
    if ledger.exists():
        assert all(value not in ledger.read_text() for value in (*ENV.values(), *world.tokens, BODY.decode()))
    result = json.loads(response.output)
    assert response.exit_code == result["exit_status"], response.output
    return result, root


def remote(world):
    return world.managers[-1].store


def released(world):
    assert remote(world).get("write-lease.json") is None


def test_cloud_check_only_probes_and_does_not_acquire_lease(world):
    response = CliRunner().invoke(cli.app, ["cloud", "check"])
    result = json.loads(response.output)
    assert response.exit_code == 0
    assert result["storage_probe"] == {"supported": True, "check": "complete"}
    assert not world.tokens and not world.components
    assert remote(world).list() == []


def test_failed_probe_stops_before_lease_or_state_write(world, monkeypatch):
    from datumoj.core.state_s3 import S3StateObjectStore

    original = S3StateObjectStore.put_if_match

    def ignores_match(self, key, data, etag):
        return self._put(key, data)

    monkeypatch.setattr(S3StateObjectStore, "put_if_match", ignores_match)
    result, root = invoke(world)
    assert result["exit_status"] == 1
    assert result["reason"] == "storage_conditional_unsupported"
    assert result["phase"] == "storage_probe"
    assert result["storage_probe"]["check"] == "put_if_match_wrong_etag"
    assert not world.tokens and not world.components and not root.exists()
    assert remote(world).list() == []
    # Demonstrate the same configuration can run after fixing storage semantics.
    monkeypatch.setattr(S3StateObjectStore, "put_if_match", original)
    result, _ = invoke(world)
    assert result["status"] == "completed"


def rows(root):
    return [json.loads(line) for line in (root / "runs.jsonl").read_text().splitlines()]


def assert_pulled(world, work, expected_generation):
    local = world.tmp / "local"
    result = CliRunner().invoke(cli.app, ["pull", "--data-dir", str(local)])
    assert result.exit_code == 0, result.output
    assert all(value not in result.output for value in (*ENV.values(), *world.tokens, BODY.decode()))
    assert json.loads((local / "state-pull.json").read_text())["generation"] == expected_generation
    for filename, table in (("manifest.sqlite", "snapshots"), ("chunks.sqlite", "chunks")):
        with sqlite3.connect(work / filename) as original, sqlite3.connect(local / filename) as replica:
            assert original.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() == replica.execute(
                f"SELECT * FROM {table} ORDER BY 1").fetchall()
    with sqlite3.connect(work / "keyword.sqlite") as original, sqlite3.connect(local / "keyword.sqlite") as replica:
        columns = "chunk_id,doc_key,source_id,published_at,effective_at"
        assert set(original.execute(f"SELECT {columns} FROM entries")) == set(
            replica.execute(f"SELECT {columns} FROM entries"))
    assert (work / "runs.jsonl").read_bytes() == (local / "runs.jsonl").read_bytes()
    assert (work / "control.json").read_bytes() == (local / "control.json").read_bytes()


def test_first_second_round_due_and_local_pull(world):
    first, work = invoke(world)
    assert first["status"] == "completed" and first["generation"] == 1
    assert first["new"] == first["parsed"] == 3
    assert {r["source_id"] for r in first["sources"]} == {s.id for s in world.specs}
    released(world)
    assert_pulled(world, work, 1)
    world.clock[0] += timedelta(hours=1)
    second, work2 = invoke(world, "work2")
    assert second["status"] == "completed" and second["generation"] == 2
    assert [r["source_id"] for r in second["sources"]] == ["jp-cloud0", "jp-cloud2"]
    assert len(rows(work2)) == 10
    released(world)
    assert_pulled(world, work2, 2)


def test_lease_held_skips_and_does_not_touch_work(world):
    manager = wiring.build_state(world.tmp / "unused")
    leases = LeaseManager(manager.store)
    lease = leases.acquire("local/competitor")
    result, root = invoke(world)
    assert result["status"] == "skipped" and result["exit_status"] == 0 and "跳过" in result["reason"]
    assert not root.exists() and remote(world).get("current.json") is None
    leases.check(lease)
    leases.release(lease.holder, lease.token)


@pytest.mark.parametrize("failure", ["exception", "report", "parse", "index", "recovery"])
def test_one_failed_source_still_publishes_other_two_and_failed_ledger(world, monkeypatch, failure):
    original_parser = FakeParser.parse

    def parse(self, snapshot, data, meta=None):
        if snapshot.source_id == "jp-cloud1" and failure == "parse":
            logging.error("%s", " ".join(ENV.values()))
            print(BODY.decode())
            raise RuntimeError(" ".join(ENV.values()))
        return original_parser(self, snapshot, data, meta)

    monkeypatch.setattr(FakeParser, "parse", parse)
    original_build = wiring.build

    def build(root):
        deps = original_build(root)
        crawl = deps.run_crawl
        add = deps.keyword.add
        index_failures = 0

        def run(spec, *args, **kwargs):
            if spec.id == "jp-cloud1" and failure == "exception":
                raise RuntimeError(" ".join(ENV.values()))
            report = crawl(spec, *args, **kwargs)
            if spec.id == "jp-cloud1" and failure == "report":
                report.failures = {" ".join(ENV.values()): 1}
            return report

        def index(chunks):
            nonlocal index_failures
            limit = 2 if failure == "recovery" else 1 if failure == "index" else 0
            if chunks and chunks[0].source_id == "jp-cloud1" and index_failures < limit:
                index_failures += 1
                raise RuntimeError(" ".join(ENV.values()))
            return add(chunks)

        deps.run_crawl = run
        monkeypatch.setattr(deps.keyword, "add", index)
        return deps

    monkeypatch.setattr(wiring, "build", build)
    result, work = invoke(world)
    assert result["generation"] == 1 and result["exit_status"] == 1
    assert [row["source_id"] for row in result["failed_sources"]] == ["jp-cloud1"]
    records = rows(work)
    failed = [row for row in records if row["source_id"] == "jp-cloud1" and row["state"] == "final"]
    assert len(failed) == 1 and failed[0]["status"] == "failed" and failed[0]["exit_status"] == 1
    assert failed[0]["failures"] and failed[0]["stop_reason"] == "source_failed"
    assert len([row for row in records if row["status"] == "completed"]) == 2
    generation = world.managers[-1].list()[0]
    assert set(generation.last_successful_runs) == {"jp-cloud0", "jp-cloud2"}
    released(world)
    assert_pulled(world, work, 1)


@pytest.mark.parametrize("failure", ["renew", "publish", "restore", "initialization", "due", "maintenance"])
def test_whole_round_failure_preserves_current_and_releases(world, monkeypatch, failure):
    first, _ = invoke(world, "initial")
    assert first["generation"] == 1
    before = remote(world).get("current.json")
    world.clock[0] += timedelta(hours=1)

    def fail(*args, **kwargs):
        logging.error("%s", " ".join(ENV.values()))
        print(BODY.decode())
        raise RuntimeError(" ".join(ENV.values()))

    if failure == "renew":
        monkeypatch.setattr(LeaseManager, "renew", fail)
    elif failure == "publish":
        monkeypatch.setattr(StateManager, "push", fail)
    elif failure == "restore":
        monkeypatch.setattr(StateManager, "restore", fail)
    elif failure == "initialization":
        monkeypatch.setattr(wiring, "build", fail)
    elif failure == "due":
        monkeypatch.setattr(schedule.Scheduler, "due", fail)
    else:
        monkeypatch.setattr(cloud, "maintain_vectors", fail)
    result, _ = invoke(world)
    assert result["exit_status"] == 1 and result["generation"] is None
    assert remote(world).get("current.json") == before
    assert len(world.managers[-1].list()) == 1
    released(world)


def test_time_budget_publishes_finished_sources_only(world, monkeypatch):
    timer = [0]
    monkeypatch.setattr(cloud, "time", SimpleNamespace(monotonic=lambda: timer[0]))
    original = cloud._source

    def source(*args):
        result = original(*args)
        timer[0] += 61
        return result

    monkeypatch.setattr(cloud, "_source", source)
    result, _ = invoke(world, "work", "--max-minutes", "1")
    assert result["budget_exhausted"] and result["generation"] == 1 and result["exit_status"] == 0
    assert [row["source_id"] for row in result["sources"]] == ["jp-cloud0"]
    released(world)


def test_zero_budget_publishes_no_new_sources(world):
    result, _ = invoke(world, "work", "--max-minutes", "0")
    assert result["generation"] == 1 and result["budget_exhausted"] and not result["sources"]
    released(world)


def test_nonempty_workspace_is_preserved(world):
    root = world.tmp / "work"
    root.mkdir()
    (root / "keep").write_bytes(b"keep")
    result, _ = invoke(world)
    assert result["reason"] == "work_directory_failed" and result["exit_status"] == 1
    assert (root / "keep").read_bytes() == b"keep"
    assert not world.managers


def test_staged_recovery_failure_blocks_publish(world, monkeypatch):
    original = cloud._source

    def source(deps, *args):
        result = original(deps, *args)
        snapshot = deps.manifest.snapshots_for("jp-cloud0")[0]
        deps.manifest.record_publish_state(snapshot.snapshot_id, PublishState.staged)
        # A missing parse artifact makes 107's recovery leave this staged.
        deps.manifest._db.execute("DELETE FROM parsed")
        return result

    monkeypatch.setattr(cloud, "_source", source)
    result, _ = invoke(world)
    assert result["reason"] == "staged_recovery_failed" and result["generation"] is None
    assert remote(world).get("current.json") is None
    released(world)


def test_staged_recovery_uses_existing_artifacts_and_publishes(world, monkeypatch):
    original = cloud._source

    def source(deps, *args):
        result = original(deps, *args)
        for snap in deps.manifest.snapshots_for("jp-cloud2"):
            deps.manifest.record_publish_state(snap.snapshot_id, PublishState.staged)
        return result

    monkeypatch.setattr(cloud, "_source", source)
    result, work = invoke(world)
    assert result["generation"] == 1
    with sqlite3.connect(work / "manifest.sqlite") as db:
        assert not db.execute("SELECT 1 FROM publish_states WHERE state='staged'").fetchall()
    released(world)


def test_round_embedding_limit_shared_across_sources_and_restored(world):
    world.overrides.pop("vectors")
    embedder = FakeEmbedder()
    world.overrides["embedder"] = embedder
    result, _ = invoke(world, "work", "--max-embed", "1")
    assert result["generation"] == 1 and embedder.embedded == 1
    coverage = world.managers[-1].list()[0].vector_coverage
    assert coverage.embedded_chunks == 1 and coverage.pending_chunks == 5
    released(world)


def test_orphan_is_resumed_before_new_work(world, monkeypatch):
    original = StateManager._publish

    def fail(*args):
        raise PreconditionFailed("synthetic lost CAS")

    monkeypatch.setattr(StateManager, "_publish", fail)
    failed, _ = invoke(world)
    assert failed["generation"] is None
    store = remote(world)
    assert store.get("generations/1.json") is not None and store.get("current.json") is None
    released(world)
    monkeypatch.setattr(StateManager, "_publish", original)
    world.clock[0] += timedelta(hours=1)
    result, _ = invoke(world, "retry")
    assert result["generation"] == 2 and len(result["sources"]) == 2
    released(world)


def test_lease_rechecked_at_pointer_publication(world, monkeypatch):
    original = LeaseManager.check

    def check(self, lease):
        original(self, lease)
        if self.store.get("generations/1.json") is not None:
            raise LeaseError("synthetic lease loss before CAS")

    monkeypatch.setattr(LeaseManager, "check", check)
    result, _ = invoke(world)
    assert result["generation"] is None and result["reason"] == "publish_failed"
    assert remote(world).get("current.json") is None
    assert remote(world).get("generations/1.json") is not None
    released(world)


def test_acquisition_transport_failure_is_failure_not_contention_skip(world, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError(" ".join(ENV.values()))

    monkeypatch.setattr(LeaseManager, "acquire", fail)
    result, root = invoke(world)
    assert result["exit_status"] == 1 and result["reason"] == "lease_acquire_failed"
    assert not root.exists()
    released(world)


def test_scheduler_public_append_validates_and_respects_readonly(world):
    root = world.tmp / "ledger"
    scheduler = schedule.Scheduler(root, world.specs, lambda spec: None)
    with pytest.raises(ValueError):
        scheduler.append_run({})
    root.mkdir(exist_ok=True)
    (root / "state-pull.json").write_text("{}")
    with pytest.raises(schedule.ReadOnlyReplicaError):
        scheduler.append_run({"source_id": "jp-cloud0", "started_at": world.clock[0].isoformat(),
                              "status": "failed", "exit_status": 1})
