"""Desired retry-persistence behavior for issue #134; diagnostic, not a fix."""
import json

from tests.test_cloud_embed import invoke_embed, seed
from tests.test_cloud_run import FlakyEmbedder, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414


def test_all_failed_rounds_keep_counts_after_fresh_restore(world):
    seed(world)
    initial = remote(world).get("current.json")
    world.overrides["embedder"] = FlakyEmbedder(100)
    counts = []
    for attempt in range(1, 5):
        result = invoke_embed(world, f"failed-{attempt}")
        assert result["generation"] is None
        assert remote(world).get("current.json") == initial
        state = json.loads((world.tmp / f"failed-{attempt}" / "vectors-state.json").read_text())
        counts.append(sorted(item["failures"] for item in state["missing"].values()))
        released(world)
    assert counts == [[1, 1, 1], [2, 2, 2], [3, 3, 3], [3, 3, 3]], counts
    assert world.overrides["embedder"].attempted == 18
