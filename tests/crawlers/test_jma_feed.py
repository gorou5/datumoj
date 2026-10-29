from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from lxml import etree

from datumoj.rikolto.core.contracts import ParseError, SourceSpec
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.jma_feed import FEED_URL, JmaFeedCrawler
from datumoj.rikolto.parsers.jma_xml import JmaXmlParser
from datumoj.rikolto.testing.contract_suites import CrawlerSuite, ParserSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, drive_crawler
from datumoj.rikolto.testing.samples import T0, ParseSample, make_snapshot, make_spec

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures" / "jma"
ATOM = "http://www.w3.org/2005/Atom"


def _feed_ids(data: bytes) -> list[str]:
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    return [item.text.strip() for item in root.findall(f"{{{ATOM}}}entry/{{{ATOM}}}id") if item.text]


def _spec(*, max_files: int = 10_000) -> SourceSpec:
    return make_spec(
        source_id="jp-jma",
        kind="jma_feed",
        base_url=FEED_URL,
        allowed_hosts=["www.data.jma.go.jp"],
        max_files=max_files,
        scope={"feeds": [FEED_URL]},
    )


class TestJmaCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return JmaFeedCrawler()

    @pytest.fixture
    def spec(self):
        return _spec(max_files=1)

    @pytest.fixture
    def make_fetcher(self):
        feed = (FIXTURES / "regular_l.snapshot1.xml").read_bytes()
        return lambda: FakeHttpFetcher({FEED_URL: feed}, now=lambda: T0)


class TestJmaParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return JmaXmlParser()

    @pytest.fixture
    def samples(self):
        names = (
            "20261020074609_0_VPFG50_460100.xml",
            "20261020112204_0_VPWP50_050000.xml",
            "20261020085325_0_VPFD51_240000.xml",
        )
        result = []
        for name in names:
            data = (FIXTURES / name).read_bytes()
            url = f"https://www.data.jma.go.jp/developer/xml/data/{name}"
            snapshot = make_snapshot("jp-jma", url, data, snapshot_id=f"jp-jma-{name}")
            result.append(ParseSample(snapshot, data, {"resource_type": "entry", "entry_id": url}))
        return result

    @pytest.fixture
    def bad_samples(self):
        return [(FIXTURES / "entry-doctype-entity.synthetic.xml").read_bytes()]


def test_fixed_window_snapshot_delta_uses_entry_ids():
    first = (FIXTURES / "regular_l.snapshot1.xml").read_bytes()
    second = (FIXTURES / "regular_l.snapshot2.xml").read_bytes()
    first_root = etree.fromstring(first)
    second_root = etree.fromstring(second)
    first_ids, second_ids = set(_feed_ids(first)), set(_feed_ids(second))
    expected_new = {
        "https://www.data.jma.go.jp/developer/xml/data/20261020125057_0_VPWS50_010000.xml",
        "https://www.data.jma.go.jp/developer/xml/data/20261020124058_0_VPWS50_010000.xml",
        "https://www.data.jma.go.jp/developer/xml/data/20261020123052_0_VPWS50_010000.xml",
        "https://www.data.jma.go.jp/developer/xml/data/20261020122055_0_VPWS50_010000.xml",
        "https://www.data.jma.go.jp/developer/xml/data/20261020121101_0_VPWS50_010000.xml",
        "https://www.data.jma.go.jp/developer/xml/data/20261020120128_0_VPWS50_010000.xml",
    }
    assert len(first) == len(second) and len(first_ids) == len(second_ids) == 9383
    assert first_root.find(f"{{{ATOM}}}updated").text != second_root.find(f"{{{ATOM}}}updated").text
    assert first_root.find(f"{{{ATOM}}}id").text != second_root.find(f"{{{ATOM}}}id").text
    assert first_ids & second_ids and second_ids - first_ids == expected_new

    manifest, raw = FakeManifest(), FakeRawStore()
    raw.put(first)
    old_feed = make_snapshot("jp-jma", FEED_URL, first, snapshot_id="prior-feed", fetched_at=T0)
    manifest.record_snapshot(old_feed, {"resource_type": "feed"})
    item_data = b"previously saved JMA message"
    raw.put(item_data)
    for index, entry_id in enumerate(first_ids):
        saved = make_snapshot(
            "jp-jma", entry_id, item_data, snapshot_id=f"prior-entry-{index}", fetched_at=T0,
        )
        manifest.record_snapshot(saved, {"resource_type": "entry", "entry_id": entry_id})

    message = (FIXTURES / "20261020074609_0_VPFG50_460100.xml").read_bytes()
    routes = {FEED_URL: second, **{entry_id: message for entry_id in expected_new}}
    steps = drive_crawler(
        JmaFeedCrawler(), _spec(), FakeHttpFetcher(routes, now=lambda: T0),
        manifest=manifest, raw=raw, now=T0,
    )
    assert len(steps[1:]) == 6
    assert {step.task.meta["entry_id"] for step in steps[1:]} == expected_new
    assert steps[0].task.url == FEED_URL
    assert steps[0].snapshot.outcome is FetchOutcome.ok
    assert all(step.task.meta["resource_type"] == "entry" for step in steps[1:])


def test_source_config_declares_hourly_feed():
    source = yaml.safe_load((Path(__file__).parents[2] / "sources" / "jp-jma.yaml").read_text(encoding="utf-8"))
    spec = SourceSpec.model_validate(source)
    assert spec.schedule == "hourly"
    assert spec.kind == "jma_feed"
    assert spec.scope["feeds"] == [FEED_URL]


def test_changing_feed_id_does_not_redownload_saved_entry():
    entry_id = "https://www.data.jma.go.jp/developer/xml/data/sample.xml"
    first = f'''<feed xmlns="{ATOM}"><updated>2026-10-20T20:51:31+09:00</updated><id>feed#long_1</id>
      <entry><id>{entry_id}</id><updated>2026-10-20T11:50:00Z</updated>
      <link type="application/xml" href="{entry_id}"/></entry></feed>'''.encode()
    second = first.replace(b"20:51:31", b"21:51:31").replace(b"feed#long_1", b"feed#long_2")
    message = (FIXTURES / "20261020074609_0_VPFG50_460100.xml").read_bytes()
    manifest, raw = FakeManifest(), FakeRawStore()
    spec = _spec()

    first_run = drive_crawler(
        JmaFeedCrawler(), spec, FakeHttpFetcher({FEED_URL: first, entry_id: message}, now=lambda: T0),
        manifest=manifest, raw=raw, now=T0,
    )
    second_run = drive_crawler(
        JmaFeedCrawler(), spec, FakeHttpFetcher({FEED_URL: second, entry_id: message}, now=lambda: T0),
        manifest=manifest, raw=raw, now=T0,
    )
    assert [step.task.url for step in first_run] == [FEED_URL, entry_id]
    assert [step.task.url for step in second_run] == [FEED_URL]


def test_optional_fields_preserve_document_metadata_and_locator():
    data = (FIXTURES / "20261020074609_0_VPFG50_460100.xml").read_bytes()
    url = "https://www.data.jma.go.jp/developer/xml/data/20261020074609_0_VPFG50_460100.xml"
    snapshot = make_snapshot("jp-jma", url, data)
    result = JmaXmlParser().parse(snapshot, data, {"entry_id": url})
    document, = result.documents
    chunk, = result.chunks
    assert document.published_at == date(2026, 10, 20)
    assert document.metadata["publishing_office"] == "鹿児島地方気象台"
    assert document.metadata["target_area"] == "鹿児島県"
    assert chunk.locator == f"entry={url}"
    assert chunk.chunk_id == f"{document.doc_key}#{chunk.locator}"
    assert "薩摩" in chunk.text


def test_parser_rejects_dtd_and_entity_expansion():
    data = (FIXTURES / "entry-doctype-entity.synthetic.xml").read_bytes()
    snapshot = make_snapshot("jp-jma", "https://www.data.jma.go.jp/developer/xml/data/bad.xml", data)
    with pytest.raises(ParseError):
        JmaXmlParser().parse(snapshot, data)


def test_feed_snapshot_does_not_become_a_document():
    data = (FIXTURES / "regular_l.snapshot1.xml").read_bytes()
    snapshot = make_snapshot("jp-jma", FEED_URL, data)
    assert JmaXmlParser().parse(snapshot, data).documents == []
