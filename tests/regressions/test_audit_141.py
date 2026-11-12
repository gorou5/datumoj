"""JMA retention is anchored to the feed, with entirely synthetic offline data."""

from datetime import timedelta

import pytest

from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.jma_feed import ATOM, FEED_URL, JmaFeedCrawler
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, drive_crawler
from datumoj.rikolto.testing.samples import T0, make_spec

URLS = [f"https://www.data.jma.go.jp/developer/xml/data/entry-{index}.xml" for index in range(4)]
UPDATED = "2026-10-20T21:00:00+09:00"
# Deliberately interleave old and recent entries: feed order is not a contract.
TIMES = ["2026-10-16T12:00:00Z", "2026-10-19T12:00:00Z",
         "2026-10-17T11:59:59Z", "2026-10-18T12:00:00Z"]


def _feed(times=TIMES, updated=UPDATED):
    entries = []
    for url, value in zip(URLS, times, strict=False):
        timestamp = "" if value is None else f"<updated>{value}</updated>"
        entries.append(f'<entry><id>{url}</id>{timestamp}<link type="application/xml" href="{url}"/></entry>')
    return f'<feed xmlns="{ATOM}"><updated>{updated}</updated>{"".join(entries)}</feed>'.encode()


def _spec(days=3):
    scope = {"feeds": [FEED_URL]}
    if days is not None:
        scope["feed_lookback_days"] = days
    return make_spec("jp-jma", kind="jma_feed", base_url=FEED_URL,
                     allowed_hosts=["www.data.jma.go.jp"], scope=scope)


def _run(feed, spec, *, limit=None, manifest=None, raw=None, now=T0):
    fetcher = FakeHttpFetcher({FEED_URL: feed, **{url: b"synthetic JMA message" for url in URLS}}, now=lambda: now)
    steps = drive_crawler(JmaFeedCrawler(), spec, fetcher, limit=limit, manifest=manifest, raw=raw, now=now)
    assert all(step.snapshot.outcome is FetchOutcome.ok for step in steps)
    assert fetcher.requests == [step.task.url for step in steps]
    return [step.task.url for step in steps[1:]]


@pytest.mark.parametrize("clock_offset", [-3650, 3650])
def test_only_two_retained_entries_are_fetched_without_old_snapshots(clock_offset):
    manifest = FakeManifest()
    assert _run(_feed(), _spec(), manifest=manifest, now=T0 + timedelta(days=clock_offset)) == [URLS[1], URLS[3]]
    assert [snap.resource_url for snap in manifest.snapshots_for("jp-jma")] == [FEED_URL, URLS[1], URLS[3]]


@pytest.mark.parametrize("boundary", ["2026-10-17T12:00:00Z", "2026-10-17T05:00:00-07:00"])
def test_exact_cutoff_is_included_across_timezones(boundary):
    assert _run(_feed([boundary, "2026-10-17T21:00:00.000001+09:00",
                       "2026-10-17T20:59:59.999999+09:00"]), _spec()) == URLS[:2]


@pytest.mark.parametrize("entry_updated", [None, "not-a-date", "2026-10-10T12:00:00", ""])
def test_unknown_entry_time_is_fetched(entry_updated):
    assert _run(_feed([entry_updated]), _spec()) == URLS[:1]


@pytest.mark.parametrize("feed_updated", ["not-a-date", "2026-10-20T21:00:00"])
def test_unparseable_or_naive_feed_time_disables_cutoff(feed_updated):
    assert _run(_feed(updated=feed_updated), _spec()) == URLS


def test_no_retention_configuration_fetches_entire_window():
    assert _run(_feed(), _spec(days=None)) == URLS


@pytest.mark.parametrize("days", [0, -3])
def test_nonpositive_retention_does_not_filter(days):
    # SourceSpec rejects these values on load; exercise the crawler guard alone.
    spec = _spec(days=None)
    spec = spec.model_copy(update={"scope": {**spec.scope, "feed_lookback_days": days}})
    assert _run(_feed(), spec) == URLS


@pytest.mark.parametrize("limit, expected", [(0, []), (1, [URLS[1]]), (2, [URLS[1], URLS[3]])])
def test_limit_only_counts_scheduled_entries(limit, expected):
    assert _run(_feed(), _spec(), limit=limit) == expected


def test_unchanged_feed_with_only_old_unsaved_entries_does_not_schedule():
    manifest, raw = FakeManifest(), FakeRawStore()
    spec, feed = _spec(), _feed()
    assert _run(feed, spec, manifest=manifest, raw=raw) == [URLS[1], URLS[3]]
    assert _run(feed, spec, manifest=manifest, raw=raw) == []
    assert len(manifest.snapshots_for("jp-jma")) == 4  # Two feed snapshots, two retained entries.


def test_unchanged_feed_still_fetches_unsaved_retained_entries_after_limit():
    manifest, raw = FakeManifest(), FakeRawStore()
    spec, feed = _spec(), _feed()
    assert _run(feed, spec, limit=1, manifest=manifest, raw=raw) == [URLS[1]]
    assert _run(feed, spec, limit=1, manifest=manifest, raw=raw) == [URLS[3]]
