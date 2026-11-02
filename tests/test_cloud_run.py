"""Cloud rounds against moto and an HTTP source served only on loopback."""

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import boto3
import httpx
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud, wiring
from datumoj.rikolto.core import schedule
from datumoj.rikolto.core.contracts import EmbeddingError, FetchResult, PreconditionFailed
from datumoj.rikolto.core.lease import LeaseError, LeaseManager
from datumoj.rikolto.core.models import FetchOutcome, PublishState
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeCrawler, FakeEmbedder, FakeParser, FakeRegistry
from datumoj.rikolto.testing.samples import make_spec

ENV = {
    "R2_ACCESS_KEY_ID": "synthetic-cloud-access",
    "R2_SECRET_ACCESS_KEY": "synthetic-cloud-secret",
    "R2_ENDPOINT": "https://s3.amazonaws.com",
    "DATUMOJ_S3_BUCKET": "synthetic-cloud-bucket",
    "DATUMOJ_S3_PREFIX": "synthetic-cloud-prefix",
    "DATUMOJ_S3_REGION": "us-east-1",
    "DATUMOJ_RAW_BACKEND": "s3",
}
BODY = b"Synthetic private source body\n\nSecond paragraph"


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
    found = remote(world).get("write-lease.json")
    if found is None:
        assert not world.tokens  # Acquisition can fail before any lease is created.
    else:
        record = json.loads(found[0])
        assert set(record) == {"released_at"} and "token" not in record
    assert LeaseManager(remote(world)).status() == {"held": False}


def test_cloud_check_only_probes_and_does_not_acquire_lease(world):
    response = CliRunner().invoke(cli.app, ["cloud", "check"])
    result = json.loads(response.output)
    assert response.exit_code == 0
    assert result["storage_probe"] == {"supported": True, "check": "complete"}
    assert not world.tokens and not world.components
    assert remote(world).list() == []


def test_failed_probe_stops_before_lease_or_state_write(world, monkeypatch):
    from datumoj.rikolto.core.state_s3 import S3StateObjectStore

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


def test_deadline_during_crawl_parses_publishes_and_retries_next_round(world, monkeypatch):
    timer = [0]
    monkeypatch.setattr(cloud, "time", SimpleNamespace(monotonic=lambda: timer[0]))
    original_build = wiring.build
    fetch = world.overrides["fetcher"].fetch
    slow = [True]
    limits = []

    def slow_fetch(*args, **kwargs):
        response = fetch(*args, **kwargs)
        if slow[0]:
            timer[0] += 110
        return response

    monkeypatch.setattr(world.overrides["fetcher"], "fetch", slow_fetch)

    def build(root):
        deps = original_build(root)
        timer[0] += 10  # Initialization also consumes the round's budget.
        crawl, parse = deps.run_crawl, deps.parse_pending

        def run(spec, crawler, crawl_deps):
            limits.append((spec.id, spec.scope["max_run_seconds"]))
            return crawl(spec, crawler, replace(crawl_deps, clock=lambda: timer[0]))

        def parse_pending(*args):
            summary = parse(*args)
            if slow[0]:
                timer[0] += 7  # Parsing completes even after the crawl deadline.
            return summary

        deps.run_crawl, deps.parse_pending = run, parse_pending
        return deps

    monkeypatch.setattr(wiring, "build", build)
    first, root = invoke(world, "first", "--max-minutes", "2")
    assert limits == [("jp-cloud0", 110)]
    assert timer[0] == 127
    assert first["generation"] == 1 and first["budget_exhausted"]
    assert first["status"] == "completed" and first["exit_status"] == 0
    assert first["new"] == first["parsed"] == 1 and first["unchanged"] == 0
    assert not first["failed_sources"]
    record = first["sources"][0]
    assert record["source_id"] == "jp-cloud0" and record["stop_reason"] == "deadline"
    assert record["status"] == "completed" and record["exit_status"] == 0
    assert rows(root)[-1]["status"] == "incomplete"
    assert rows(root)[-1]["started_at"] == world.clock[0].isoformat()
    assert not world.managers[-1].list()[0].last_successful_runs
    assert all("max_run_seconds" not in spec.scope for spec in world.specs)
    released(world)

    # Retry immediately, without advancing the scheduler's wall clock.
    slow[0], timer[0] = False, 0
    second, _ = invoke(world, "second", "--max-minutes", "2")
    assert second["due"] == 3
    assert [row["source_id"] for row in second["sources"]] == [spec.id for spec in world.specs]
    assert second["generation"] == 2 and second["exit_status"] == 0
    assert set(world.managers[-1].list()[0].last_successful_runs) == {spec.id for spec in world.specs}
    timer[0] = 0
    third, _ = invoke(world, "third", "--max-minutes", "2")
    assert third["due"] == 0 and third["sources"] == []
    released(world)


@pytest.mark.parametrize("seconds_left,started_count", [(59, 0), (60, 3)])
def test_source_requires_at_least_one_minute(world, monkeypatch, seconds_left, started_count):
    timer = [0]
    monkeypatch.setattr(cloud, "time", SimpleNamespace(monotonic=lambda: timer[0]))
    original_build = wiring.build

    def build(root):
        deps = original_build(root)
        timer[0] = 120 - seconds_left
        return deps

    monkeypatch.setattr(wiring, "build", build)
    result, _ = invoke(world, "work", "--max-minutes", "2")
    assert len(result["sources"]) == started_count
    assert result["budget_exhausted"] == (started_count == 0)
    assert result["generation"] == 1 and result["exit_status"] == 0
    released(world)


@pytest.mark.parametrize("scope,expected", [({}, 900), ({"max_run_seconds": 75}, 75)])
def test_round_limit_preserves_shorter_source_limit_and_scope(world, monkeypatch, scope, expected):
    original_build = wiring.build
    world.specs[0].scope.update(scope)
    seen = []

    def build(root):
        deps = original_build(root)
        crawl = deps.run_crawl

        def run(spec, *args):
            if spec.id == world.specs[0].id:
                seen.append(spec.scope)
            return crawl(spec, *args)

        deps.run_crawl = run
        return deps

    monkeypatch.setattr(wiring, "build", build)
    result, _ = invoke(world)
    assert result["exit_status"] == 0
    assert seen[0]["max_run_seconds"] == expected
    assert seen[0]["urls"] == world.specs[0].scope["urls"]
    assert world.specs[0].scope.get("max_run_seconds") == scope.get("max_run_seconds")


@pytest.mark.parametrize("options,expected", [((), 10), (("--reserve-minutes", "12.5"), 12.5)])
def test_cli_passes_reserve_minutes(monkeypatch, options, expected):
    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return {"exit_status": 0}

    monkeypatch.setattr(cloud, "run", run)
    response = CliRunner().invoke(cli.app, ["cloud", "run", *options])
    assert response.exit_code == 0
    assert calls == [{"work_dir": None, "max_embed": 1000, "max_minutes": 40,
                      "reserve_minutes": expected}]


def test_content_lease_check_is_throttled_with_injected_monotonic_clock():
    from concurrent.futures import ThreadPoolExecutor

    ticks = [10.0]
    calls = []
    check = cloud._throttled_check(lambda: calls.append(ticks[0]), monotonic=lambda: ticks[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: check(), range(100)))
    assert calls == [10.0]
    ticks[0] = 39.999
    check()
    assert calls == [10.0]
    ticks[0] = 40.0
    check()
    ticks[0] = 71.0
    check()
    assert calls == [10.0, 40.0, 71.0]


@pytest.mark.parametrize("boundary", ["generation", "publish"])
def test_full_lease_checks_bypass_content_throttle(world, monkeypatch, boundary):
    original_build = wiring.build_state
    checks = []

    def build(root):
        manager = original_build(root)
        check_write = manager._check_write
        publish = manager._publish

        def checked(*, content=False):
            before = len(checks)
            check_write(content=content)
            if not content:
                assert len(checks) > before

        def before_publish(candidate, etag):
            before = len(checks)
            publish(candidate, etag)
            assert len(checks) >= before + 2  # before_publish and pointer write.

        manager._check_write = checked
        manager._publish = before_publish
        return manager

    original_check = LeaseManager.check

    def check(self, lease):
        original_check(self, lease)
        checks.append(1)
        # All uploads are done but no generation exists: force a loss specifically
        # at the unthrottled generation boundary, after the content check succeeded.
        content = self.store.list("copies/")
        generation = self.store.get("generations/1.json")
        if (boundary == "generation" and content and generation is None
                or boundary == "publish" and generation is not None):
            raise LeaseError("synthetic loss")

    monkeypatch.setattr(wiring, "build_state", build)
    monkeypatch.setattr(LeaseManager, "check", check)
    result, _ = invoke(world)
    assert result["reason"] == "publish_failed" and result["generation"] is None
    assert remote(world).get("current.json") is None
    assert (remote(world).get("generations/1.json") is None) == (boundary == "generation")
    assert result["publish_timings"]["total"] >= 0
    released(world)


def test_cloud_transfer_workers_and_safe_timings_survive_release_hook(world):
    result, _ = invoke(world)
    assert result["status"] == "completed"
    assert world.managers[-1].transfer_workers == 8
    assert result["restore_timings"] == {}
    timings = result["publish_timings"]
    assert {"upload_copies", "upload_raw", "write_generation", "publish", "cleanup", "total",
            "post_publish", "post_publish_download_copies", "post_publish_download_raw"} <= timings.keys()
    assert timings["total"] >= timings["post_publish"] >= timings["post_publish_download_raw"]
    world.clock[0] += timedelta(hours=1)
    restored, _ = invoke(world, "next")
    assert restored["status"] == "completed"
    assert {"download_copies", "download_raw", "cleanup", "total"} <= restored["restore_timings"].keys()
    for key in ("restore_timings", "publish_timings"):
        assert all(type(stage) is str and type(seconds) in (int, float) and seconds >= 0
                   for stage, seconds in restored[key].items())
    released(world)


def test_post_publish_restore_delay_is_visible(world, monkeypatch):
    from datumoj.rikolto.core.state_s3 import S3StateObjectStore

    monotonic = cloud.time.monotonic
    original_get = S3StateObjectStore.get
    delay = [0.0]

    def get(store, key):
        result = original_get(store, key)
        # Raw gets happen in wiring's published-copy restore after pointer CAS.
        if key.startswith("raw/"):
            delay[0] += 120
        return result

    monkeypatch.setattr(cloud.time, "monotonic", lambda: monotonic() + delay[0])
    monkeypatch.setattr(S3StateObjectStore, "get", get)
    result, _ = invoke(world)
    assert result["status"] == "completed" and delay[0] >= 120
    timings = result["publish_timings"]
    assert timings["post_publish_download_raw"] >= delay[0]
    assert timings["total"] >= timings["post_publish"] >= delay[0]
    released(world)


class FlakyEmbedder(FakeEmbedder):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures
        self.attempted = 0

    def embed(self, texts):
        texts = list(texts)
        self.attempted += len(texts)
        if self.failures:
            self.failures -= 1
            raise EmbeddingError("synthetic transient embedding failure")
        return super().embed(texts)


def test_embedding_error_is_deferred_then_retried_next_round(world):
    world.overrides.pop("vectors")
    embedder = FlakyEmbedder(1)
    world.overrides["embedder"] = embedder
    first, root = invoke(world, "first")
    assert first["exit_status"] == 0 and first["failed_sources"] == []
    assert first["embedding_deferred"] == 1 and first["vector_pending"] == 2
    assert first["sources"][0]["embedding_deferred"] == 1
    assert first["sources"][0]["failures"] == {}
    assert first["backfill_embedded"] == 0 and embedder.attempted == 6
    state = json.loads((root / "vectors-state.json").read_text())
    assert [item["failures"] for item in state["missing"].values()] == [1]
    # No source is due yet; backfill still repairs this error next round.
    second, repaired = invoke(world, "second")
    assert second["due"] == 0 and second["sources"] == []
    assert second["backfill_embedded"] == 2 and second["vector_pending"] == 0
    assert second["embedding_deferred"] == 0 and second["exit_status"] == 0
    assert json.loads((repaired / "vectors-state.json").read_text())["missing"] == {}
    released(world)


def test_source_fails_only_on_third_embedding_error(world):
    world.overrides.pop("vectors")
    # Every source fails once per due round, including the daily source.
    embedder = FlakyEmbedder(100)
    world.overrides["embedder"] = embedder
    world.clock[0] -= timedelta(days=4)
    for attempt in (1, 2, 3, 4):
        if attempt > 1:
            world.clock[0] += timedelta(days=1)
        result, root = invoke(world, f"round{attempt}")
        assert result["exit_status"] == int(attempt == 3)
        assert result["embedding_deferred"] == (3 if attempt < 3 else 0)
        assert len(result["failed_sources"]) == (3 if attempt == 3 else 0)
        if attempt == 3:
            assert all(row["failures"] == {"embedding_failed": 1} for row in result["sources"])
        state = json.loads((root / "vectors-state.json").read_text())
        assert {item["failures"] for item in state["missing"].values()} == {min(attempt, 3)}
        assert result["vector_pending"] == 6
    assert embedder.attempted == 18
    released(world)


def test_failed_embedding_attempts_also_consume_shared_allowance(world):
    world.overrides.pop("vectors")
    embedder = FlakyEmbedder(100)
    world.overrides["embedder"] = embedder
    first, _ = invoke(world, "first", "--max-embed", "3")
    assert first["exit_status"] == 0 and first["embedding_deferred"] == 2
    assert first["backfill_embedded"] == 0 and first["vector_pending"] == 6
    assert embedder.attempted == 3
    second, _ = invoke(world, "second", "--max-embed", "1")
    assert second["exit_status"] == 0 and second["embedding_deferred"] == 1
    assert second["backfill_embedded"] == 0 and second["vector_pending"] == 6
    assert embedder.attempted == 4
    released(world)


@pytest.mark.parametrize("max_minutes,expected", [(1.99, 0), (2, 3)])
def test_backfill_all_sources_respects_time_and_shared_allowance(world, monkeypatch, max_minutes, expected):
    world.overrides.pop("vectors")
    embedder = FakeEmbedder()
    world.overrides["embedder"] = embedder
    first, _ = invoke(world, "initial", "--max-embed", "1")
    assert first["vector_pending"] == 5
    assert embedder.embedded == 1
    # Freeze the round clock: exactly two minutes is sufficient.
    monkeypatch.setattr(cloud, "time", SimpleNamespace(monotonic=lambda: 0))
    second, root = invoke(world, "second", "--max-embed", "3", "--max-minutes", str(max_minutes))
    assert second["due"] == 0 and not second["sources"]
    assert second["backfill_embedded"] == expected
    assert second["vector_pending"] == 5 - expected
    assert embedder.embedded == 1 + expected
    coverage = world.managers[-1].list()[0].vector_coverage
    assert coverage.embedded_chunks == 1 + expected and coverage.pending_chunks == 5 - expected
    assert sum(item["chunks"] for item in json.loads((root / "vectors-state.json").read_text())["missing"].values()) == (
        5 - expected
    )
    released(world)


def test_backfill_error_reaches_limit_without_unpublishing_documents(world):
    world.overrides.pop("vectors")
    world.overrides["embedder"] = FlakyEmbedder(100)
    for attempt in (1, 2, 3, 4):
        result, root = invoke(world, f"round{attempt}")
        assert result["exit_status"] == int(attempt == 3)
        assert result["embedding_deferred"] == (3 if attempt < 3 else 0)
        assert result["vector_pending"] == 6
        if attempt > 1:
            assert result["due"] == 0
        if attempt == 3:
            assert [row["reasons"] for row in result["failed_sources"]] == [{"embedding_failed": 1}] * 3
        with sqlite3.connect(root / "manifest.sqlite") as db:
            assert db.execute("SELECT count(*) FROM publish_states WHERE state='published'").fetchone()[0] == 3
    assert world.overrides["embedder"].attempted == 18
    released(world)


def test_backfill_checks_time_between_batches_and_runs_after_sources(world, monkeypatch):
    class ManyChunks(FakeParser):
        def parse(self, snapshot, data, meta=None):
            return super().parse(snapshot, b"\n\n".join([b"synthetic paragraph"] * 40), meta)

    world.overrides.pop("vectors")
    world.overrides["embedder"] = FakeEmbedder()
    world.overrides["parsers"] = {("fake", "text/plain"): ManyChunks}
    first, _ = invoke(world, "initial", "--max-embed", "0")
    assert first["vector_pending"] == 120
    world.clock[0] += timedelta(hours=1)
    timer, calls, sources = [0], [], []
    monkeypatch.setattr(cloud, "time", SimpleNamespace(monotonic=lambda: timer[0]))
    source = cloud._source

    def tracked_source(*args):
        row, remaining = source(*args)
        sources.append(row["source_id"])
        # Exercise both phases using one allowance while leaving backlog.
        timer[0] += 5
        return row, remaining

    monkeypatch.setattr(cloud, "_source", tracked_source)
    retry = cloud._retry_vectors

    def slow_retry(pipeline, *args, **kwargs):
        assert sources == ["jp-cloud0", "jp-cloud2"]
        calls.append((timer[0], pipeline.vector_limit))
        embedded = retry(pipeline, *args, **kwargs)
        timer[0] += 190
        return embedded

    monkeypatch.setattr(cloud, "_retry_vectors", slow_retry)
    result, _ = invoke(world, "second", "--max-embed", "200", "--max-minutes", "5")
    assert result["exit_status"] == 0 and result["backfill_embedded"] == 32
    assert result["vector_pending"] == 8  # 80 during sources, 32 during backfill.
    assert calls == [(10, 32)] and timer[0] < 300
    assert world.overrides["embedder"].embedded == 112
    released(world)
