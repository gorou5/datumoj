"""A recurring deadline on the first alphabetical source must not starve others."""

from dataclasses import replace
from types import SimpleNamespace

from datumoj.rikolto import cloud, wiring
from tests.test_cloud_run import invoke, released, rows
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.


def test_recurring_deadline_allows_later_sources_to_run_within_two_rounds(world, monkeypatch):
    timer = [0]
    monkeypatch.setattr(cloud, "time", SimpleNamespace(monotonic=lambda: timer[0]))
    fetch = world.overrides["fetcher"].fetch
    large = min(world.specs, key=lambda spec: spec.id)
    large_url = large.scope["urls"][0]

    def slow_fetch(url, *args, **kwargs):
        response = fetch(url, *args, **kwargs)
        if url == large_url:
            timer[0] += 121  # This source consumes the entire two-minute round, every time.
        return response

    monkeypatch.setattr(world.overrides["fetcher"], "fetch", slow_fetch)
    original_build = wiring.build

    def build(root):
        deps = original_build(root)
        crawl = deps.run_crawl

        def run(spec, crawler, crawl_deps):
            return crawl(spec, crawler, replace(crawl_deps, clock=lambda: timer[0]))

        deps.run_crawl = run
        return deps

    monkeypatch.setattr(wiring, "build", build)
    rounds = []
    for index in range(2):
        timer[0] = 0
        result, root = invoke(world, f"round-{index}", "--max-minutes", "2")
        assert result["due"] == 3 and result["generation"] == index + 1
        # The flag means sources were skipped; a deadline on the final source
        # leaves no sources to skip, so it stays false in the second round.
        assert result["budget_exhausted"] == (index == 0)
        assert result["exit_status"] == 0
        assert result["sources"][-1]["source_id"] == large.id
        assert result["sources"][-1]["stop_reason"] == "deadline"
        assert rows(root)[-1]["status"] == "incomplete"
        rounds.append([row["source_id"] for row in result["sources"]])
        released(world)

    assert rounds[0] == [large.id]
    assert rounds[1] == [world.specs[1].id, world.specs[2].id, large.id]
    assert {source_id for ran in rounds for source_id in ran} == {spec.id for spec in world.specs}
