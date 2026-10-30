"""MAPLE fixture-only retrieval evaluation; no answer generation or model service.

Use real new-source parsers and the 501 keyword retriever. Stores are contract
fakes. Dataset/observation text projections below are test-only search material,
not production ingestion. WDI pagination and the extracted NDB member require
explicit derived snapshots; their original recordings remain in the inventory.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
import yaml

from datumoj.rikolto.core.contracts import SourceSpec
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import FetchOutcome, SourceText
from datumoj.rikolto.eval import evaluate, load_questions, write_reports
from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.index.hybrid import HybridRetriever
from datumoj.rikolto.parsers.abr import AbrParser
from datumoj.rikolto.parsers.data_europa import DataEuropaParser
from datumoj.rikolto.parsers.estat_tables import EstatTableParser
from datumoj.rikolto.parsers.eurostat import EurostatParser, read_structure
from datumoj.rikolto.parsers.fedreg import FedregParser
from datumoj.rikolto.parsers.ksj import KsjParser
from datumoj.rikolto.parsers.mhlw_ndb import MEMBER, MhlwNdbParser
from datumoj.rikolto.parsers.udata import UdataParser
from datumoj.rikolto.parsers.wdi import WDIParser
from datumoj.rikolto.testing.fakes import (
    FakeChunkStore,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import make_chunk, make_document, make_snapshot

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests/fixtures"
BANKS = {"west": 12, "jp": 8, "multilingual": 6, "compare": 4}
SOURCES = ("us-fedreg", "eu-eurostat", "intl-wdi", "fr-data-gouv", "eu-data-europa",
           "jp-ksj", "jp-census", "jp-mhlw", "jp-abr")


@pytest.fixture(scope="module")
def maple_corpus(tmp_path_factory):
    work = tmp_path_factory.mktemp("maple-eval")
    specs = {sid: SourceSpec.model_validate(yaml.safe_load((ROOT / f"sources/{sid}.yaml").read_text()))
             for sid in SOURCES}
    chunks, manifest, raw = FakeChunkStore(), FakeManifest(), FakeRawStore()
    observations = FakeObservationStore()
    evidence, inventory, parsed_rows, warnings = {}, [], [], []

    def ingest(sid, parser, snapshot, body, meta=None):
        raw.put(body)
        manifest.record_snapshot(snapshot, meta or {})
        result = parser.parse(snapshot, body, meta)
        warnings.extend(result.warnings)
        for doc in result.documents:
            doc_chunks = [c for c in result.chunks if c.doc_key == doc.doc_key]
            chunks.put_document(doc, doc_chunks)
            for chunk in doc_chunks:
                evidence[(snapshot.sha256, chunk.locator)] = SourceText(content=chunk.text)
        # Mirror catalog/row fields only; never add translations or question text.
        projections = defaultdict(list)
        for dataset in result.datasets:
            locator = dataset.locator or "entry=metadata"
            text = "\n".join([dataset.title, dataset.description or "", *dataset.tags])
            projections[dataset.dataset_key].append(make_chunk(
                dataset.dataset_key, snapshot.sha256, locator, text,
                title_path=[dataset.title], lang="fr" if sid == "fr-data-gouv" else "en"))
        for obs in result.observations:
            text = obs.model_dump_json()
            projections[obs.dataset_key].append(make_chunk(
                obs.dataset_key, snapshot.sha256, obs.locator, text,
                title_path=[obs.survey, obs.indicator], lang="en" if sid in SOURCES[:3] else "ja"))
            parsed_rows.append(obs)
        for key, projected in projections.items():
            existing = chunks.chunks_for(key)
            locators = {c.locator for c in existing}
            combined = [*existing, *(c for c in projected if c.locator not in locators)]
            # One current document cannot mix snapshots: metadata refreshes for
            # the same WDI key retain the row snapshot in the retrieval corpus.
            if existing and existing[0].snapshot_sha256 != snapshot.sha256:
                continue
            chunks.put_document(make_document(key, snapshot.sha256, key), combined)
            for chunk in projected:
                evidence[(snapshot.sha256, chunk.locator)] = SourceText(content=chunk.text)

    def recording(folder, name, sid):
        records = json.loads((FIX / folder / "_responses.json").read_bytes())
        entry = records[name]
        record = entry.get("extracted_from", entry)
        path = FIX / folder / name
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        assert digest == entry["sha256"]
        inventory.append({"path": str(path.relative_to(ROOT)), "url": record["url"],
                          "sha256": digest, "source_id": sid, "sample_type": entry["sample_type"],
                          "extracted_from": entry.get("extracted_from")})
        # Synthetic allow-all robots isolates replay from policy discovery. Every
        # content response uses its recorded URL, headers and untouched bytes.
        # NDB is explicitly a derived member replay, NOT the original ZIP body.
        robots = work / "robots.synthetic.txt"
        robots.write_text("User-agent: *\nAllow: /\n")
        origin = httpx.URL(record["url"]).copy_with(path="/robots.txt", query=None)
        transport = ReplayTransport({str(origin): Recorded(robots), record["url"]: Recorded(
            path, headers=record["headers"])})
        fetched_at = datetime.fromisoformat(record["fetched_at"])
        with PoliteHttpFetcher.from_spec(specs[sid], transport=transport, now=lambda: fetched_at,
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            response = fetcher.fetch(record["url"])
        assert response.outcome is FetchOutcome.ok and response.content == body
        return response.to_snapshot(f"maple-{len(inventory)}", sid), body

    def parse_record(folder, name, sid, parser, meta=None):
        snapshot, body = recording(folder, name, sid)
        ingest(sid, parser, snapshot, body, meta)

    def forbidden(*args, **kwargs):
        pytest.fail("MAPLE evaluation must never access the network")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
        detail = json.loads((FIX / "fedreg/document_2026-20122.json").read_bytes())
        parse_record("fedreg", "full_text_2026-20122.xml", "us-fedreg", FedregParser(), detail)
        for code, name in (("nama_10_gdp", "nama_10_gdp_DE-FR-NL_2020-2024.xml"),
                           ("gov_10dd_edpt1", "gov_10dd_edpt1_EL_2000-2015.xml")):
            _, structure = recording("eurostat", f"datastructure_{code.upper()}.xml", "eu-eurostat")
            parse_record("eurostat", name, "eu-eurostat", EurostatParser(),
                         {"structure": read_structure(structure, code)})
        pages = [recording("wdi", f"gdp_cd_cn_JPN-USA_2020-2024_p{page}.json", "intl-wdi")
                 for page in (1, 2)]
        payloads = [json.loads(body, parse_float=Decimal) for _, body in pages]
        assert [p[0]["page"] for p in payloads] == [1, 2]
        assert all(p[0]["pages"] == 2 and p[0]["total"] == 20 for p in payloads)
        rows = [row for payload in payloads for row in payload[1]]
        # Decimal tokens become exact strings in this derived parser input;
        # WDIParser accepts Decimal(str(value)), without float rounding.
        body = json.dumps([{"page": 1, "pages": 1, "total": 20}, rows], default=str).encode()
        snapshot = make_snapshot("intl-wdi", pages[0][0].resource_url,
                                 body, fetched_at=pages[0][0].fetched_at)
        ingest("intl-wdi", WDIParser(), snapshot, body,
               {"request_type": "data", "countries": ["JPN", "USA"], "start_year": 2020, "end_year": 2024,
                "indicators": specs["intl-wdi"].scope["indicators"]})
        parse_record("wdi", "indicator_NY.GDP.MKTP.CN.json", "intl-wdi", WDIParser(),
                     {"request_type": "metadata", "indicator": "NY.GDP.MKTP.CN"})
        for page in (1, 2):
            parse_record("udata_fr", f"datasets_org-insee_page{page}_size2.json", "fr-data-gouv", UdataParser())
        parse_record("data_europa", "repo_dataset_combined-nomenclature-2023.jsonld", "eu-data-europa",
                     DataEuropaParser(), {"role": "dataset", "dataset_id": "combined-nomenclature-2023"})
        parse_record("ksj", "P05-22_41_GML.zip", "jp-ksj", KsjParser())
        parse_record("census2024", "c1_001_1.xlsx", "jp-census", EstatTableParser(),
                     specs["jp-census"].scope["tables"][0])
        original, member = recording("ndb", "ndb11_kenshin_shitsumon01_pref_sex_age.xlsx", "jp-mhlw")
        archive = io.BytesIO()
        with ZipFile(archive, "w", ZIP_DEFLATED) as zipped:
            zipped.writestr(MEMBER, member)
        body = archive.getvalue()
        snapshot = make_snapshot("jp-mhlw", original.resource_url, body, fetched_at=original.fetched_at)
        ingest("jp-mhlw", MhlwNdbParser(), snapshot, body, {"member": MEMBER})
        parse_record("abr", "mt_town_city134023.csv.zip", "jp-abr", AbrParser())
        for (sid, table), group in _group_observations(parsed_rows).items():
            observations.write_table(sid, table, group)
        index = SqliteKeywordIndex(":memory:")
        try:
            index.add(list(chunks.iter_chunks()))
            retriever = HybridRetriever(chunks, manifest, FakeRegistry(list(specs.values())), index)

            def cite(citation):
                raw.verify(citation.snapshot_sha256)
                return evidence.get((citation.snapshot_sha256, citation.locator))

            yield {"retriever": retriever, "observations": observations, "cite": cite,
                   "manifest": manifest, "inventory": inventory, "warnings": warnings,
                   "rows": parsed_rows, "chunks": chunks}
        finally:
            index.close()


def _group_observations(rows):
    tables = defaultdict(list)
    for obs in rows:
        tables[(obs.source_id, obs.table_key)].append(obs)
    return tables


def test_question_coverage():
    questions = [q for q in load_questions(ROOT / "eval/questions") if q.id.startswith("maple-")]
    assert len(questions) == 33
    assert len({q.id for q in questions}) == 33
    for category, count in BANKS.items():
        selected = [q for q in questions if Path(q._source_file).stem == f"maple_{category}"]
        positive = [q for q in selected if q.expect.hits]
        assert len(positive) == count
        assert all(q.expect.gap_state is not None and q.notes.startswith("Fixture:") for q in positive)
    west = [q for q in questions if q.id.startswith("maple-west-")]
    for source in SOURCES[:5]:
        assert sum(source in q.source_ids for q in west) >= 2
    japanese = [q for q in questions if q.id.startswith("maple-jp-")]
    for source in SOURCES[5:]:
        assert sum(source in q.source_ids for q in japanese) == 2
    assert {q.lang for q in questions if q.id.startswith("maple-ml-")} == {"en", "fr", "ja", "zh"}
    assert sum(q.expect.gap_state.value == "zero_hits" and q.expect.hits == [] for q in questions) == 3
    comparisons = [q for q in questions if q.kind == "cross_source"]
    assert len(comparisons) == 4
    assert all("不换算" in q.notes for q in comparisons)
    assert all("jp-estat-api" not in (q.source_ids or []) for q in questions)


def test_recordings_and_original_facts(maple_corpus):
    corpus = maple_corpus
    assert len(corpus["inventory"]) == 15
    for item in corpus["inventory"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    counts = {sid: sum(o.source_id == sid for o in corpus["rows"]) for sid in SOURCES}
    assert {sid: n for sid, n in counts.items() if n} == {
        "eu-eurostat": 79, "intl-wdi": 20, "jp-census": 5886, "jp-mhlw": 1536}
    wdi = next(o for o in corpus["rows"] if o.source_id == "intl-wdi" and o.locator == "row=1")
    assert wdi.value == Decimal("4190008188358.57")
    # Record the fake SQL store's precision boundary without weakening the
    # numeric question: evaluate() will report it if exact equality is lost.
    ndb = [o for o in corpus["rows"] if o.source_id == "jp-mhlw"]
    assert all(o.value is not None for o in ndb)  # The recording has NO suppressed data cells.
    assert all(o.time_label == "2023年度" and str(o.released_at) == "2026-06-16" for o in ndb)
    abr = list(corpus["chunks"].iter_chunks(["jp-abr"]))
    assert len(abr) == 4 and '"machiaza_id": "0003000"' in abr[0].text
    ksj = list(corpus["chunks"].iter_chunks(["jp-ksj"]))
    assert len(ksj) == 21 and any("嘉瀬公民館" in c.text for c in ksj)


@pytest.mark.parametrize("category", BANKS)
def test_maple_category_gate(maple_corpus, category, tmp_path):
    corpus = maple_corpus
    questions = [q for q in load_questions(ROOT / "eval/questions")
                 if Path(q._source_file).stem == f"maple_{category}"]
    report = evaluate(questions, retriever=corpus["retriever"], observation_store=corpus["observations"],
                      cite=corpus["cite"], manifest=corpus["manifest"], k=10)
    evidence_dir = os.environ.get("DATUMOJ_MAPLE_EVIDENCE_DIR")
    directory = Path(evidence_dir) / category if evidence_dir else tmp_path / category
    write_reports(report, directory)
    (directory / "corpus.json").write_text(json.dumps(
        {"inventory": corpus["inventory"], "parser_warnings": corpus["warnings"],
         "retrieval": "501 keyword-only; test-only dataset and observation projections; contract fake stores",
         "derived": {"wdi": "both recorded pages concatenated, Decimal values preserved as exact strings",
                     "ndb": "unchanged extracted XLSX wrapped in a new ZIP; original ZIP not recorded"},
         "cross_language": "explicit translated queries; no automatic translation or bge-m3 claim"},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Emit evidence before enforcing the hard category gates, including all
    # partial misses and numeric failures. A tolerable miss stays a failure row.
    print(f"MAPLE {category}: Recall@10={report.overall['recall_at_10']:.3f}, "
          f"gap_accuracy={report.overall['gap_accuracy']:.3f}; failures={report.failures}")
    assert report.overall["recall_at_10"] >= 0.8, report.failures
    assert report.overall["gap_accuracy"] == 1.0, report.failures
