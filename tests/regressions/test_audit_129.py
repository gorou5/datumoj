"""Slow post-crawl work must yield at budget + grace and publish resumable state."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.testing.fakes import FakeEmbedder, FakeParser
from tests.test_cloud_run import invoke, released, rows
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.


@pytest.mark.parametrize("phase", ["parse", "embed"])
def test_post_crawl_deadline_publishes_within_round_budget_plus_grace(world, monkeypatch, phase):
    timer = [0]
    deadline = 120 + cloud._PIPELINE_GRACE_SECONDS
    monkeypatch.setattr(cloud, "time", SimpleNamespace(monotonic=lambda: timer[0]))

    class SlowParser(FakeParser):
        def parse(self, snapshot, data, meta=None):
            if phase == "parse":
                timer[0] = deadline
            return super().parse(snapshot, b"\n\n".join([b"synthetic paragraph"] * 501), meta)

    class SlowEmbedder(FakeEmbedder):
        def embed(self, texts):
            result = super().embed(texts)
            timer[0] = deadline
            return result

    world.overrides["parsers"] = {("fake", "text/plain"): SlowParser}
    if phase == "embed":
        world.overrides.pop("vectors")
        world.overrides["embedder"] = SlowEmbedder()
    # A second snapshot must remain unparsed, rather than getting a failed record.
    spec = world.specs[0]
    spec.scope["urls"].append(spec.scope["urls"][0] + "?page=2")
    result, root = invoke(world, phase, "--max-minutes", "2")
    assert timer[0] <= deadline
    assert result["exit_status"] == 0 and result["generation"] == 1
    assert result["parsed"] == 1 and result["budget_exhausted"]
    assert result["sources"][0]["stop_reason"] == "deadline"
    assert rows(root)[-1]["status"] == "incomplete"
    assert not world.managers[-1].list()[0].last_successful_runs
    with sqlite3.connect(root / "manifest.sqlite") as db:
        assert db.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM parsed").fetchone()[0] == 1
    if phase == "embed":
        missing = json.loads((root / "vectors-state.json").read_text())["missing"]
        assert sum(item["chunks"] for item in missing.values()) == 1
        assert all(item["failures"] == 0 for item in missing.values())
    released(world)
