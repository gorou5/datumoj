"""Embedding retries expose only counters and sanitized reasons, including backfill."""

import io
import json
import logging
import sys

from datumoj.rikolto import cloud
from datumoj.rikolto.core.contracts import EmbeddingError
from datumoj.rikolto.testing.fakes import FakeEmbedder
from tests.test_cloud_run import invoke, released
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.


def test_embedding_errors_are_safe_in_progress_result_and_persisted_state(world, monkeypatch):
    private = ("https://synthetic.invalid/audit-124", "/synthetic/private/audit-124", "synthetic-secret-124")
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)

    class BrokenEmbedder(FakeEmbedder):
        def embed(self, texts):
            message = " ".join(private)
            print(message)
            print(message, file=sys.stderr)
            logging.getLogger(__name__).error("%s", message)
            raise EmbeddingError(message)

    world.overrides.pop("vectors")
    world.overrides["embedder"] = BrokenEmbedder()
    for attempt in (1, 2, 3):
        result, root = invoke(world, f"round{attempt}")
        assert result["exit_status"] == int(attempt == 3)
        state = (root / "vectors-state.json").read_text()
        assert all(value not in state + json.dumps(result) + stream.getvalue() for value in private)
        assert {item["error"] for item in json.loads(state)["missing"].values()} == {"EmbeddingError"}
        assert {item["failures"] for item in json.loads(state)["missing"].values()} == {attempt}
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    allowed = {"event", "source_id", "elapsed_s", "new", "unchanged", "parsed", "failures", "stop_reason",
               "publish_timings", "restore_timings", "embedding_deferred", "backfill_embedded", "vector_pending"}
    assert all(set(row) <= allowed for row in events)
    assert {row["event"] for row in events} >= {"source_start", "source_end", "backfill_start", "backfill_end"}
    first_end = next(row for row in events if row["event"] == "source_end")
    assert first_end["failures"] == {} and first_end["embedding_deferred"] == 1
    assert events[-1]["failures"] == {"embedding_failed": 3}
    released(world)


def test_progress_ignores_non_whitelisted_record_fields(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)
    cloud._progress("backfill_end", cloud.time.monotonic(), {
        "embedding_deferred": 2, "backfill_embedded": 32, "vector_pending": 7,
        "error": "synthetic-private-exception", "unexpected": "synthetic-private-source",
    })
    row = json.loads(stream.getvalue())
    assert set(row) == {"event", "source_id", "elapsed_s", "new", "unchanged", "parsed", "failures",
                        "stop_reason", "publish_timings", "restore_timings", "embedding_deferred",
                        "backfill_embedded", "vector_pending"}
    assert "synthetic-private" not in stream.getvalue()
    stream.seek(0)
    stream.truncate()
    cloud._progress("backfill_start", cloud.time.monotonic())
    row = json.loads(stream.getvalue())
    assert row["embedding_deferred"] == row["backfill_embedded"] == row["vector_pending"] == 0
