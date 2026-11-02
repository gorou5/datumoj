"""Cloud embedding preserves output privacy and shares the crawl writer lease."""

import io
import json
import logging
import sys

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.core.contracts import EmbeddingError
from datumoj.rikolto.core.lease import LeaseManager
from datumoj.rikolto.testing.fakes import FakeEmbedder
from tests.test_cloud_embed import invoke_embed, seed
from tests.test_cloud_run import invoke, released
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.

ALLOWED = {"event", "elapsed_s", "embedded", "vector_pending", "embedding_deferred", "embedding_failed",
           "publish_timings", "restore_timings"}


def test_embedding_exceptions_never_expose_private_values(world, monkeypatch, capsys):
    seed(world)
    private = ("https://synthetic.invalid/audit-125", "/synthetic/private/audit-125", "synthetic-secret-125")
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)

    class BrokenEmbedder(FakeEmbedder):
        def embed(self, texts):
            message = " ".join(private)
            print(message)
            print(message, file=sys.stderr)
            logging.getLogger(__name__).error("%s", message)
            raise EmbeddingError(message)

    world.overrides["embedder"] = BrokenEmbedder()
    result = invoke_embed(world)
    assert result["embedded"] == 0 and result["generation"] is None
    assert result["embedding_deferred"] == 3 and result["exit_status"] == 0
    state = (world.tmp / "embed/vectors-state.json").read_text()
    captured = capsys.readouterr()
    output = state + json.dumps(result) + stream.getvalue() + captured.out + captured.err
    assert all(value not in output for value in private)
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [row["event"] for row in events] == ["embed_start", "embed_batch", "round_end"]
    assert all(set(row) == ALLOWED for row in events)
    released(world)


def test_embed_progress_discards_unlisted_fields(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)
    cloud._progress("embed_batch", cloud.time.monotonic(), {
        "embedded": 32, "vector_pending": 8, "embedding_deferred": 1, "embedding_failed": 0,
        "source_id": "synthetic-private", "error": "synthetic-private", "unexpected": "synthetic-private",
    })
    assert set(json.loads(stream.getvalue())) == ALLOWED
    assert "synthetic-private" not in stream.getvalue()


@pytest.mark.parametrize("outer", ["run", "embed"])
def test_run_and_embed_cannot_hold_same_namespace_lease(world, monkeypatch, outer):
    seed(world)
    original = cloud._backfill
    observed = []

    def attempt_other(*args, **kwargs):
        store = world.managers[-1].store
        before = store.get("write-lease.json")
        active = LeaseManager(store).status()
        assert active["held"] and active["active"]
        # Both writers target this same state namespace; each restores to an
        # empty workspace, as required by the public API.
        other = invoke_embed(world, "contender") if outer == "run" else invoke(world, "contender")[0]
        assert other["status"] == "skipped" and other["exit_status"] == 0
        assert other["generation"] is None
        assert store.get("write-lease.json") == before
        observed.append(other)
        return original(*args, **kwargs)

    monkeypatch.setattr(cloud, "_backfill", attempt_other)
    result = invoke(world, "outer")[0] if outer == "run" else invoke_embed(world, "outer")
    assert result["status"] == "completed" and result["generation"] == 2
    assert len(observed) == 1
    released(world)
