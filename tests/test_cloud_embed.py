"""Independent embedding rounds use the same offline cloud world as crawl rounds."""

import io
import json
import sys
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud
from datumoj.rikolto.core.contracts import PreconditionFailed
from datumoj.rikolto.core.lease import LeaseManager
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.index.embed import HashEmbedder
from datumoj.rikolto.testing.fakes import FakeParser, FakeRegistry
from tests.test_cloud_run import FlakyEmbedder, invoke, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.


def seed(world, *, many=False):
    world.overrides.pop("vectors", None)
    world.overrides["embedder"] = HashEmbedder(model="fake-hash-v1")
    if many:
        class ManyChunks(FakeParser):
            def parse(self, snapshot, data, meta=None):
                return super().parse(snapshot, b"\n\n".join([b"synthetic paragraph"] * 40), meta)

        world.overrides["parsers"] = {("fake", "text/plain"): ManyChunks}
    result, root = invoke(world, "initial", "--max-embed", "0")
    assert result["vector_pending"] == (120 if many else 6)
    return root


def invoke_embed(world, name="embed", *options):
    response = CliRunner().invoke(cli.app, [
        "cloud", "embed", "--work-dir", str(world.tmp / name), "--max-minutes", "100", *options,
    ])
    result = json.loads(response.output)
    assert response.exit_code == result["exit_status"], response.output
    return result


def test_embeds_all_backlog_and_publishes_without_crawling_or_parsing(world, monkeypatch):
    initial = seed(world)
    ledger = (initial / "runs.jsonl").read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("crawl or parse called")

    monkeypatch.setattr(FakeParser, "parse", forbidden)
    monkeypatch.setattr(cloud, "_source", forbidden)
    monkeypatch.setattr(cloud, "_recover", forbidden)
    world.overrides["crawlers"] = {"fake": forbidden}
    world.overrides["parse_pending"] = forbidden
    result = invoke_embed(world)
    assert result["status"] == "completed" and result["generation"] == 2
    assert result["embedded"] == 6 and result["vector_pending"] == 0
    assert result["embedding_deferred"] == result["embedding_failed"] == 0
    assert (world.tmp / "embed/runs.jsonl").read_bytes() == ledger
    assert world.managers[-1].list()[0].vector_coverage.pending_chunks == 0
    assert result["publish_timings"]["total"] >= 0 and result["restore_timings"]["total"] >= 0
    released(world)


@pytest.mark.parametrize("fresh", [True, False])
def test_no_backlog_does_not_publish_and_releases_lease(world, fresh):
    if not fresh:
        seed(world)
        assert invoke_embed(world, "filled")["generation"] == 2
    before = remote(world).get("current.json") if world.managers else None
    result = invoke_embed(world)
    assert result["status"] == "completed" and result["exit_status"] == 0
    assert result["generation"] is None and result["embedded"] == result["vector_pending"] == 0
    assert result["publish_timings"] == {}
    assert remote(world).get("current.json") == before
    released(world)


def test_time_checked_between_batches_and_still_publishes(world, monkeypatch):
    seed(world, many=True)
    timer, batches = [0], []
    monkeypatch.setattr(cloud, "time", SimpleNamespace(monotonic=lambda: timer[0]))
    retry = cloud._retry_vectors

    def slow_retry(pipeline, *args, **kwargs):
        batches.append(pipeline.vector_limit)
        result = retry(pipeline, *args, **kwargs)
        timer[0] += 61
        return result

    monkeypatch.setattr(cloud, "_retry_vectors", slow_retry)
    result = invoke_embed(world, "embed", "--max-minutes", "2")
    assert batches == [32] and result["embedded"] == 32
    assert result["vector_pending"] == 88 and result["budget_exhausted"] is True
    assert result["status"] == "completed" and result["generation"] == 2
    released(world)


@pytest.mark.parametrize("limit", [0, 1, 33])
def test_max_embed_limits_total_across_batches(world, limit):
    seed(world, many=True)
    result = invoke_embed(world, "embed", "--max-embed", str(limit))
    assert result["embedded"] == limit and result["vector_pending"] == 120 - limit
    assert result["generation"] == (2 if limit else None)
    assert result["budget_exhausted"] is False
    released(world)


def test_retry_limit_fails_without_publishing_an_empty_generation(world):
    world.overrides.pop("vectors")
    world.overrides["embedder"] = FlakyEmbedder(100)
    for attempt in (1, 2):
        result, _ = invoke(world, f"seed{attempt}")
        assert result["embedding_deferred"] == 3
    before = remote(world).get("current.json")
    result = invoke_embed(world)
    assert result["status"] == "failed" and result["exit_status"] == 1
    assert result["embedding_failed"] == 3 and result["embedding_deferred"] == 0
    assert result["reason"] == "embedding_failed" and result["vector_pending"] == 6
    assert result["generation"] is None and remote(world).get("current.json") == before
    assert world.overrides["embedder"].attempted == 18  # Once per document per round.
    released(world)


def test_transient_errors_are_deferred_once_and_success_still_publishes(world):
    seed(world)
    world.overrides["embedder"] = FlakyEmbedder(1)
    result = invoke_embed(world)
    assert result["status"] == "completed" and result["generation"] == 2
    assert result["embedding_deferred"] == 1 and result["embedding_failed"] == 0
    assert result["embedded"] == 4 and result["vector_pending"] == 2
    assert world.overrides["embedder"].attempted == 6
    released(world)


def test_held_lease_skips_before_initialization(world):
    manager = cloud.wiring.build_state(world.tmp / "unused")
    leases = LeaseManager(manager.store)
    lease = leases.acquire("local/synthetic-competitor")
    try:
        result = invoke_embed(world)
        assert result["status"] == "skipped" and result["exit_status"] == 0
        assert result["generation"] is None and not world.components
        assert not (world.tmp / "embed").exists()
        leases.check(lease)
    finally:
        leases.release(lease.holder, lease.token)


def test_release_failure_is_failed_even_without_backlog(world, monkeypatch):
    def broken_release(*args):
        raise RuntimeError("synthetic release failure")

    monkeypatch.setattr(LeaseManager, "release", broken_release)
    result = invoke_embed(world)
    assert result["status"] == "failed" and result["exit_status"] == 1
    assert result["reason"] == "lease_release_failed"


@pytest.mark.parametrize("code", [0, 1])
@pytest.mark.parametrize("limited", [False, True])
def test_cli_passes_options_prints_json_and_exits(monkeypatch, tmp_path, code, limited):
    seen = []
    expected = {"status": "failed" if code else "completed", "exit_status": code}
    monkeypatch.setattr(cloud, "embed", lambda **kw: seen.append(kw) or expected)
    options = ["--max-embed", "42"] if limited else []
    response = CliRunner().invoke(cli.app, [
        "cloud", "embed", "--work-dir", str(tmp_path), "--max-minutes", "100",
        "--reserve-minutes", "15", *options,
    ])
    assert seen == [{"work_dir": tmp_path, "max_minutes": 100, "reserve_minutes": 15,
                     "max_embed": 42 if limited else None}]
    assert response.exit_code == code and json.loads(response.output) == expected


def test_progress_one_line_per_batch(world, monkeypatch):
    seed(world, many=True)
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)
    result = invoke_embed(world, "embed", "--max-embed", "65")
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [row["event"] for row in events] == [
        "embed_start", "embed_batch", "embed_batch", "embed_batch", "publish_start", "publish_end", "round_end",
    ]
    assert [row["embedded"] for row in events if row["event"] == "embed_batch"] == [32, 64, 65]
    assert events[-1]["embedded"] == result["embedded"] == 65


def test_orphan_candidate_is_resumed_before_embedding(world, monkeypatch):
    publish = StateManager._publish

    def fail_publish(*args):
        raise PreconditionFailed("synthetic lost CAS")

    monkeypatch.setattr(StateManager, "_publish", fail_publish)
    seed(world)
    assert remote(world).get("generations/1.json") is not None
    assert remote(world).get("current.json") is None
    monkeypatch.setattr(StateManager, "_publish", publish)
    result = invoke_embed(world)
    assert result["status"] == "completed" and result["generation"] == 2
    assert result["embedded"] == 6 and result["vector_pending"] == 0
    released(world)


@pytest.mark.parametrize("seconds", [59, 60])
def test_batch_requires_one_minute(world, monkeypatch, seconds):
    seed(world)
    monkeypatch.setattr(cloud, "time", SimpleNamespace(monotonic=lambda: 0))
    result = invoke_embed(world, "embed", "--max-minutes", str(seconds / 60))
    assert result["embedded"] == (6 if seconds == 60 else 0)
    assert result["budget_exhausted"] == (seconds < 60)
    assert result["generation"] == (2 if seconds == 60 else None)
    released(world)


def test_reaching_retry_limit_still_publishes_other_successes(world):
    world.overrides.pop("vectors")
    world.overrides["embedder"] = FlakyEmbedder(100)
    for attempt in (1, 2):
        invoke(world, f"seed{attempt}")
    world.overrides["embedder"] = FlakyEmbedder(1)
    result = invoke_embed(world)
    assert result["status"] == "failed" and result["exit_status"] == 1
    assert result["generation"] == 3 and result["embedded"] == 4
    assert result["embedding_failed"] == 1 and result["embedding_deferred"] == 0
    assert result["vector_pending"] == 2 and result["reason"] == "embedding_failed"
    released(world)


@pytest.mark.parametrize("disable_all", [False, True])
def test_disabled_backlog_is_skipped_and_cleanup_is_published(world, disable_all):
    seed(world)
    disabled = {spec.id for spec in world.specs} if disable_all else {world.specs[0].id}
    world.overrides["registry"] = FakeRegistry([
        spec.model_copy(update={"scope": {**spec.scope, "embed": False}}) if spec.id in disabled else spec
        for spec in world.specs
    ])
    result = invoke_embed(world, "disabled", "--max-embed", "1")
    assert result["status"] == "completed" and result["generation"] == 2
    assert result["embedded"] == (0 if disable_all else 1)
    assert result["vector_pending"] == (0 if disable_all else 3)
    assert result["embedding_deferred"] == result["embedding_failed"] == 0
    pending = json.loads((world.tmp / "disabled/vectors-state.json").read_text())["missing"]
    assert all(not any(key.startswith(source_id + ":") for source_id in disabled) for key in pending)
    assert world.managers[-1].list()[0].vector_coverage.pending_chunks == result["vector_pending"]
    if disable_all:
        assert invoke_embed(world, "cleaned")["generation"] is None
    released(world)


def test_embed_no_cleanup_reports_and_preserves_orphan(world, monkeypatch):
    seed(world)
    monkeypatch.setenv("R2_RAW_CLEANUP", "on")
    store = remote(world)
    store.put("copies/sha256/synthetic-orphan", b"orphan")
    result = invoke_embed(world, "no-cleanup", "--no-cleanup")
    assert result["status"] == "completed" and result["generation"] == 2
    assert result["cleanup"]["enabled"] is False and result["cleanup"]["raw_enabled"] is True
    assert result["cleanup"]["reclaimable_copies"] >= 1 and result["cleanup"]["copies_deleted"] == 0
    assert store.exists("copies/sha256/synthetic-orphan")
    released(world)
