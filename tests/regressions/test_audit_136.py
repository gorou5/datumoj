"""Audit 136: synthetic, offline corrections, bounded catch-up and SDMX children."""

import json
from collections import Counter
from datetime import date, timedelta

import pytest

from datumoj.rikolto.core.contracts import CrawlContext, FetchResult, ParseError
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.eurostat import EurostatCrawler
from datumoj.rikolto.crawlers.fedreg import FedregCrawler, _window
from datumoj.rikolto.parsers.eurostat import EurostatParser, read_structure
from datumoj.rikolto.parsers.fedreg import FedregParser
from datumoj.rikolto.testing.fakes import (
    FakeChunkStore,
    FakeHttpFetcher,
    FakeManifest,
    FakeRawStore,
    FakeResponse,
    drive_crawler,
)
from datumoj.rikolto.testing.samples import T0, make_snapshot
from tests.crawlers.test_eurostat import SPEC as EUROSTAT
from tests.crawlers.test_fedreg import SPEC as FEDREG


def context(spec=FEDREG, manifest=None, raw=None, day=0):
    return CrawlContext(spec, manifest or FakeManifest(), raw or FakeRawStore(), T0 + timedelta(days=day))


def detail(number):
    return {
        "document_number": number, "type": "Notice", "title": "Synthetic audit notice",
        "publication_date": "2026-10-01",
        "full_text_xml_url": f"https://www.federalregister.gov/documents/full_text/xml/{number}.xml",
    }


def xml(number):
    return f"<NOTICE><P>Synthetic correction.</P><FRDOC>FR Doc. {number} Filed</FRDOC></NOTICE>".encode()


def search(records, *, count=None, pages=1):
    return json.dumps({"count": len(records) if count is None else count,
                       "total_pages": pages, "results": records}).encode()


@pytest.mark.parametrize("number", ["2026-19812", "C1-2026-19812", "C2-2026-19812", "C12-2026-19812"])
def test_regular_and_correction_numbers_crawl_and_parse(number):
    root = next(iter(FedregCrawler().plan(context())))
    fetcher = FakeHttpFetcher({
        root.url: FakeResponse(200, search([detail(number)])),
        FEDREG.base_url + f"documents/{number}.json": FakeResponse(200, json.dumps(detail(number)).encode()),
        detail(number)["full_text_xml_url"]: FakeResponse(200, xml(number)),
    })
    steps = drive_crawler(FedregCrawler(), FEDREG, fetcher, now=T0)
    assert [s.task.meta["role"] for s in steps] == ["search", "detail", "xml"]
    final = steps[-1]
    parsed = FedregParser().parse(final.snapshot, final.result.content, final.task.meta)
    assert parsed.documents[0].doc_key == f"us-fedreg:{number}"
    assert parsed.chunks[0].text == "Synthetic correction."


@pytest.mark.parametrize("number", [
    None, 42, "", "C0-2026-1", "C-2026-1", "c1-2026-1", "X1-2026-1", "2026-1\n",
    "２０２６-1", "2026-١", "../2026-1", "C1-2026-1/../../x", "2026-1?x=y", "2026-1#x", "2026-1%2f",
])
def test_invalid_number_is_warned_and_skipped_but_parser_rejects_it(number, caplog):
    ctx = context()
    crawler = FedregCrawler()
    root = next(iter(crawler.plan(ctx)))
    body = search([detail("2026-1"), detail(number), detail("C1-2026-1")])
    children = list(crawler.after_fetch(root, FetchResult(
        url=root.url, content=body, fetched_at=T0, outcome=FetchOutcome.ok), ctx))
    assert [task.meta["document_number"] for task in children] == ["2026-1", "C1-2026-1"]
    assert "Skipping invalid Federal Register document number" in caplog.text
    assert any(record.levelname == "WARNING" for record in caplog.records)
    body = xml("2026-1")
    with pytest.raises(ParseError, match="document_number"):
        FedregParser().parse(make_snapshot(FEDREG.id, detail("2026-1")["full_text_xml_url"], body), body, detail(number))


@pytest.mark.parametrize("payload", [
    {}, {"total_pages": 1, "results": []}, {"count": 1, "results": []},
    {"count": 1, "total_pages": 1}, {"count": 10000, "total_pages": 50},
    {"count": 1, "total_pages": 1, "results": {}},
    {"count": 1, "total_pages": 1, "results": []},
    {"count": True, "total_pages": 1, "results": []},
])
def test_invalid_search_envelope_still_fails(payload):
    crawler, ctx = FedregCrawler(), context()
    task = next(iter(crawler.plan(ctx)))
    with pytest.raises(ValueError):
        list(crawler.after_fetch(task, FetchResult(
            url=task.url, fetched_at=T0, outcome=FetchOutcome.ok,
            content=json.dumps(payload).encode()), ctx))


def test_correction_embedded_number_mismatch_is_rejected():
    body = xml("C2-2026-19812")
    with pytest.raises(ParseError, match="number mismatch"):
        FedregParser().parse(make_snapshot(FEDREG.id, detail("C1-2026-19812")["full_text_xml_url"], body),
                             body, detail("C1-2026-19812"))


@pytest.mark.parametrize("budget", [1, 2, 3, 4])
@pytest.mark.parametrize("failed_role", [None, "detail", "xml"])
def test_multiple_limited_rounds_ingest_every_document_once(budget, failed_role):
    manifest, raw, store = FakeManifest(), FakeRawStore(), FakeChunkStore()
    spec = FEDREG.model_copy(update={"max_files": budget})
    numbers = ["2026-1", "C1-2026-2", "2026-3", "2026-4", "C2-2026-5"]
    docs = [detail(number) for number in numbers]
    root = next(iter(FedregCrawler().plan(context(spec))))
    page2 = root.url.replace("page=1&", "page=2&")
    routes = {root.url: FakeResponse(200, search(docs[:3], count=5, pages=2)),
              page2: FakeResponse(200, search(docs[3:], count=5, pages=2))}
    for doc in docs:
        number = doc["document_number"]
        routes[FEDREG.base_url + f"documents/{number}.json"] = FakeResponse(200, json.dumps(doc).encode())
        routes[doc["full_text_xml_url"]] = FakeResponse(200, xml(number))
    failed_url = (FEDREG.base_url + "documents/2026-1.json" if failed_role == "detail"
                  else docs[0]["full_text_xml_url"])
    attempts, ingested, round_sizes = Counter(), [], []
    failed_once = False
    for day in range(30):
        # New crawler and fetcher every round: progress must survive in the stores.
        crawler = FedregCrawler()
        before = next(iter(crawler.plan(context(spec, manifest, raw, day))))
        assert before.meta["run"] == root.meta["run"]
        assert before.meta["start"] == root.meta["start"]
        assert before.meta["end"] == root.meta["end"]
        responses = dict(routes)
        # The pending graph no longer suppresses discovery of the current window.
        fresh = _window(context(spec, manifest, raw, day), date(2026, 10, 1), (T0 + timedelta(days=day)).date())
        responses[fresh.url] = FakeResponse(200, search(docs[:3], count=5, pages=2))
        responses[fresh.url.replace("page=1&", "page=2&")] = FakeResponse(200, search(docs[3:], count=5, pages=2))
        if failed_role and not failed_once:
            responses[failed_url] = FakeResponse(503)
        steps = drive_crawler(crawler, spec, FakeHttpFetcher(responses),
                              manifest=manifest, raw=raw, now=T0 + timedelta(days=day))
        round_sizes.append(len(steps))
        for step in steps:
            attempts[step.task.url] += 1
            if step.result.outcome is FetchOutcome.http_error:
                assert step.task.url == failed_url
                failed_once = True
            if step.task.meta["role"] == "xml" and step.result.outcome is FetchOutcome.ok:
                parsed = FedregParser().parse(step.snapshot, step.result.content, step.task.meta)
                doc, = parsed.documents
                store.put_document(doc, parsed.chunks)
                ingested.append(doc.doc_key)
        if len(ingested) == len(numbers):
            break
    assert len(round_sizes) > 1 and all(0 < size <= budget for size in round_sizes)
    assert attempts[root.url] == attempts[page2] == 1
    assert Counter(ingested) == Counter(f"us-fedreg:{number}" for number in numbers)
    assert all(store.get_document(key) is not None for key in ingested)
    for doc in docs:
        for url in (FEDREG.base_url + f"documents/{doc['document_number']}.json", doc["full_text_xml_url"]):
            assert attempts[url] == (2 if failed_role and url == failed_url else 1)
    # Only after every XML is stored does the seven-day overlap move forward.
    next_root = list(FedregCrawler().plan(context(spec, manifest, raw, day + 1)))[-1]
    assert "2026-10-13" <= next_root.meta["start"] <= (T0 + timedelta(days=day - 7)).date().isoformat()
    assert next_root.meta["end"] == (T0 + timedelta(days=day + 1)).date().isoformat()


# Synthetic SDMX children response: six codelists, including geo and unit.
SDMX = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/"
CHILDREN = (
    f'<m:Structure xmlns:m="{SDMX}message" xmlns:s="{SDMX}structure" xmlns:c="{SDMX}common">'
    '<m:Structures><s:Codelists>'
    + ''.join(f'<s:Codelist id="{dim.upper()}"><s:Code id="{code}">'
              f'<c:Name xml:lang="en">{label}</c:Name></s:Code></s:Codelist>'
              for dim, code, label in [("geo", "EL", "Synthetic Greece"), ("unit", "PC_GDP", "Synthetic percent of GDP"),
                                       ("freq", "A", "Annual"), ("sector", "S13", "Government"),
                                       ("na_item", "B9", "Deficit"), ("OBS_FLAG", "p", "Provisional")])
    + '</s:Codelists><s:DataStructures><s:DataStructure id="GOV_10DD_EDPT1">'
    '<s:DataStructureComponents><s:DimensionList>'
    + ''.join(f'<s:Dimension id="{dim}"><s:LocalRepresentation><s:Enumeration><Ref id="{dim.upper()}"/>'
              '</s:Enumeration></s:LocalRepresentation></s:Dimension>'
              for dim in ("freq", "unit", "sector", "na_item", "geo"))
    + '</s:DimensionList></s:DataStructureComponents></s:DataStructure>'
    '</s:DataStructures></m:Structures></m:Structure>'
).encode()


def test_eurostat_children_preserves_geo_and_unit_labels():
    entry = next(entry for entry in EUROSTAT.scope["datasets"] if entry["id"] == "gov_10dd_edpt1")
    spec = EUROSTAT.model_copy(update={"scope": {"datasets": [entry]}})
    ctx, crawler = context(spec), EurostatCrawler()
    task, = crawler.plan(ctx)
    assert task.url.endswith("/datastructure/ESTAT/GOV_10DD_EDPT1?references=children")
    structure = read_structure(CHILDREN, "gov_10dd_edpt1")
    data_task, = crawler.after_fetch(task, FetchResult(
        url=task.url, fetched_at=T0, outcome=FetchOutcome.ok, content=CHILDREN), ctx)
    assert data_task.meta["structure"] == structure
    assert structure["labels"]["geo"]["EL"] == "Synthetic Greece"
    assert structure["labels"]["unit"]["PC_GDP"] == "Synthetic percent of GDP"
    data = (f'<m:GenericData xmlns:m="{SDMX}message" xmlns:g="{SDMX}data/generic">'
            '<m:Header><m:DataSetID>GOV_10DD_EDPT1</m:DataSetID></m:Header>'
            '<m:DataSet><g:Series><g:SeriesKey><g:Value id="geo" value="EL"/>'
            '<g:Value id="freq" value="A"/><g:Value id="sector" value="S13"/>'
            '<g:Value id="unit" value="PC_GDP"/><g:Value id="na_item" value="B9"/>'
            '</g:SeriesKey><g:Obs><g:ObsDimension value="2020"/><g:ObsValue value="-1.2"/>'
            '</g:Obs></g:Series></m:DataSet></m:GenericData>').encode()
    parsed = EurostatParser().parse(make_snapshot(spec.id, data_task.url, data), data, data_task.meta)
    assert not parsed.warnings
    assert parsed.observations[0].area_label == "Synthetic Greece"
    assert parsed.observations[0].unit == "PC_GDP"
    assert parsed.datasets[0].extras["codelists"]["unit"]["PC_GDP"] == "Synthetic percent of GDP"
