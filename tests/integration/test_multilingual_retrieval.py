"""Offline MAPLE calibration; catalog projections are test-only, not ingestion."""

import hashlib
import json
import os
import unicodedata
from pathlib import Path

import httpx
import pytest

from datumoj.rikolto.index import fts
from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.index.hybrid import HybridRetriever
from datumoj.rikolto.parsers.data_europa import DataEuropaParser
from datumoj.rikolto.parsers.fedreg import FedregParser
from datumoj.rikolto.parsers.udata import UdataParser
from datumoj.rikolto.store.catalog import SqliteCatalogStore
from datumoj.rikolto.testing.fakes import FakeChunkStore, FakeManifest, FakeRegistry
from datumoj.rikolto.testing.samples import make_chunk, make_document, make_snapshot, make_spec

FIX = Path(__file__).parents[1] / "fixtures"
FED = "us-fedreg:2026-20122"
POP = "fr-data-gouv:6ad16a7506eea90157a47a35"
PCS = "fr-data-gouv:6ad16a7406eea90157a47a34"
TRAVEL = "fr-data-gouv:6ad16a7406eea90157a47a33"
HOME = "fr-data-gouv:6ad16a7206eea90157a47a32"
EU = "eu-data-europa:combined-nomenclature-2023"
SUP = "section=SUPPLEMENTARY INFORMATION > "

# Each relevant query names one expected document/dataset and locator prefix.
QUERIES = [
    ("en-wires", "en", "What are the hazards of the overhead electrical transmission wires?", FED,
     SUP + "II. Background and Authority;"),
    ("en-dates", "en", "What are the dates for the enforcement of the safety zones?", FED,
     SUP + "III. Discussion of the Rule;"),
    ("en-paperwork", "en", "What is the collection of information under the Paperwork Reduction Act?", FED,
     SUP + "IV. Regulatory Analyses > B. Collection of Information;"),
    ("en-small", "en", "What is the impact on the small entities?", FED,
     SUP + "IV. Regulatory Analyses > A. Impact on Small Entities;"),
    ("en-mandates", "en", "What are the requirements of the Unfunded Mandates Reform Act?", FED,
     SUP + "IV. Regulatory Analyses > D. Unfunded Mandates Reform Act;"),
    ("en-environment", "en", "What is the National Environmental Policy Act for the environment?", FED,
     SUP + "IV. Regulatory Analyses > E. Environment;"),
    ("en-nomenclature", "en", "What is the combined nomenclature for the customs tariff?", EU, "entry="),
    ("en-goods", "en", "What is the classification of goods in the nomenclature?", EU, "entry="),
    ("fr-population", "fr", "Quelle est la population par sexe et âge dans les Iris ?", POP, "entry="),
    ("fr-nationalite", "fr", "Quelles sont les données sur la nationalité et le statut d'immigré ?", POP, "entry="),
    ("fr-pcs", "fr", "Quelle est la population selon la catégorie socioprofessionnelle ?", PCS, "entry="),
    ("fr-travail", "fr", "Quel est le mode de transport pour aller travailler ?", TRAVEL, "entry="),
    ("fr-menages", "fr", "Quel est le nombre de personnes dans les ménages ?", HOME, "entry="),
    ("fr-nomenclature", "fr", "Quelle est la nomenclature combinée pour les marchandises ?", EU, "entry="),
    ("en-zero-axolotl", "en", "the habitat of the axolotl", None, None),
    ("en-zero-quasar", "en", "the exoplanet in the quasar", None, None),
    ("fr-zero-plants", "fr", "la photosynthèse de la chlorophylle", None, None),
    ("fr-zero-dinosaurs", "fr", "les fossiles et les dinosaures", None, None),
]


@pytest.fixture
def corpus(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("calibration must never use the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    chunks, manifest, inventory, datasets = FakeChunkStore(), FakeManifest(), [], []

    def parse(folder, filename, source, parser, meta=None):
        data = (FIX / folder / filename).read_bytes()
        records = json.loads((FIX / folder / "_responses.json").read_bytes())
        snap = make_snapshot(source, records[filename]["url"], data)
        manifest.record_snapshot(snap, meta or {})
        inventory.append({"path": f"tests/fixtures/{folder}/{filename}", "sha256": snap.sha256})
        result = parser.parse(snap, data, meta)
        for doc in result.documents:
            chunks.put_document(doc, [c for c in result.chunks if c.doc_key == doc.doc_key])
        for dataset in result.datasets:
            # Same fields as catalog_fts: title, description, tags. No translations
            # or resource contents added; snapshot/locator still identify the recording.
            lang = "fr" if source == "fr-data-gouv" else "en"
            doc = make_document(dataset.dataset_key, snap.sha256, dataset.title, lang=lang)
            chunk = make_chunk(dataset.dataset_key, snap.sha256, dataset.locator,
                               "\n".join([dataset.description or "", *dataset.tags]),
                               title_path=[dataset.title], lang=lang)
            chunks.put_document(doc, [chunk])
        datasets.extend(result.datasets)

    detail = json.loads((FIX / "fedreg/document_2026-20122.json").read_bytes())
    parse("fedreg", "full_text_2026-20122.xml", "us-fedreg", FedregParser(), detail)
    for page in (1, 2):
        parse("udata_fr", f"datasets_org-insee_page{page}_size2.json", "fr-data-gouv", UdataParser())
    parse("data_europa", "repo_dataset_combined-nomenclature-2023.jsonld", "eu-data-europa", DataEuropaParser(),
          {"role": "dataset", "dataset_id": "combined-nomenclature-2023"})
    assert len(list(chunks.iter_chunks())) == 27
    assert len(datasets) == 5
    registry = FakeRegistry([make_spec(sid) for sid in ("us-fedreg", "fr-data-gouv", "eu-data-europa")])
    return chunks, manifest, registry, inventory, datasets


def _measure(corpus, coverage):
    chunks, manifest, registry, inventory, datasets = corpus
    index = SqliteKeywordIndex(":memory:", min_keyword_coverage=coverage)
    catalog = SqliteCatalogStore(":memory:")
    try:
        index.add(list(chunks.iter_chunks()))
        catalog.upsert(datasets)
        retriever = HybridRetriever(chunks, manifest, registry, index)
        rows = []
        for qid, lang, query, expected, locator in QUERIES:
            found = retriever.search([query], k=10)
            recalled = any(h.doc_key == expected and h.locator.startswith(locator) for h in found.data) if expected else None
            rows.append({"id": qid, "lang": lang, "query": query, "expected": expected, "locator": locator,
                         "recalled": recalled, "gap_state": found.gap_state.value,
                         "hits": [h.chunk_id for h in found.data],
                         "production_catalog_hits": [h.dataset.dataset_key for h in catalog.search(query, k=10)]})
        relevant = [r for r in rows if r["expected"]]
        absent = [r for r in rows if not r["expected"]]
        return {"coverage": coverage, "bm25": 0.01, "inventory": inventory, "queries": rows,
                "recall_at_10": sum(r["recalled"] for r in relevant) / len(relevant),
                "recall_en": sum(r["recalled"] for r in relevant if r["lang"] == "en") / 8,
                "recall_fr": sum(r["recalled"] for r in relevant if r["lang"] == "fr") / 6,
                "zero_hits_accuracy": sum(r["gap_state"] == "zero_hits" for r in absent) / len(absent)}
    finally:
        index.close()
        catalog.close()


def _legacy_terms(query):
    """Pre-501 splitter, kept here so the measured baseline can be reproduced."""
    terms, current = [], []
    for char in query:
        if char.isspace() or unicodedata.category(char).startswith("P"):
            if current:
                terms.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        terms.append("".join(current))
    return list(dict.fromkeys(terms)) or [query]


@pytest.mark.parametrize(("config", "coverage"), [
    ("legacy", 0.5), ("stop-025", 0.25), ("stop-050", 0.5), ("stop-075", 0.75),
])
def test_calibration(corpus, monkeypatch, config, coverage):
    if config == "legacy":
        monkeypatch.setattr(fts, "_query_terms", _legacy_terms)
    report = _measure(corpus, coverage)
    report["config"] = config
    assert len(report["queries"]) == 18
    assert report["recall_at_10"] == report["recall_en"] == report["recall_fr"] == 1.0
    assert report["zero_hits_accuracy"] == (0.0 if config == "legacy" else 1.0)
    for row in report["queries"]:
        if row["expected"]:
            assert row["recalled"], row
        elif config != "legacy":
            assert row["gap_state"] == "zero_hits" and not row["hits"], row
    for item in report["inventory"]:
        assert hashlib.sha256((FIX.parents[1] / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    evidence = os.environ.get("DATUMOJ_MULTILINGUAL_EVIDENCE_DIR")
    if evidence:
        directory = Path(evidence)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{config}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def test_production_catalog_and_cross_language_boundary(corpus):
    chunks, manifest, registry, _, datasets = corpus
    catalog = SqliteCatalogStore(":memory:")
    index = SqliteKeywordIndex(":memory:")
    try:
        catalog.upsert(datasets)
        for query, expected in [("population", POP), ("combined nomenclature", EU), ("nomenclature combinée", EU)]:
            assert expected in {h.dataset.dataset_key for h in catalog.search(query, k=10)}
        # No query translation; the real multilingual embedder is outside this
        # offline calibration. This documents the keyword-only Japanese→English boundary.
        index.add(list(chunks.iter_chunks()))
        found = HybridRetriever(chunks, manifest, registry, index).search(["セントローレンス川の送電線交換"])
        assert found.gap_state.value == "zero_hits" and not found.data
        assert any("degraded=true" in warning for warning in found.warnings)
    finally:
        catalog.close()
        index.close()
