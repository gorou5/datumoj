from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

import yaml

from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.models import FetchOutcome, Snapshot
from datumoj.rikolto.eval import load_questions
from datumoj.rikolto.parsers.estat_tables import EstatTableParser, TableEntry
from datumoj.rikolto.parsers.html_page import HtmlPageParser
from datumoj.rikolto.parsers.pdf import PdfParser
from datumoj.rikolto.store.catalog import SqliteCatalogStore
from datumoj.rikolto.testing.fakes import (
    FakeChunkStore,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
    FakeRetriever,
)
from datumoj.rikolto.testing.samples import make_snapshot, make_spec

ROOT = Path(__file__).parents[2]
QUESTION_DIR = ROOT / "eval" / "questions"
FIXTURES = ROOT / "tests" / "fixtures"
QUESTIONS = load_questions(QUESTION_DIR)

GOV_WEB_FIXTURES = {
    "gov-web-cao-procurement-ja": [
        (
            "gov_web/html/cao/detail-1.html",
            "https://www8.cao.go.jp/cstp/openinnovation/procurement/index.html",
            "html",
        ),
    ],
    "gov-web-cao-ecosystem-en": [
        (
            "gov_web/html/cao/detail-2.html",
            "https://www8.cao.go.jp/cstp/openinnovation/ecosystem/index.html",
            "html",
        ),
    ],
    "gov-web-soumu-dx-zh": [
        (
            "gov_web/html/soumu/detail-1.html",
            "https://www.soumu.go.jp/denshijiti/index_00001.html",
            "html",
        ),
    ],
    "gov-web-soumu-digital-talent-ja": [
        (
            "gov_web/html/soumu/detail-2.html",
            "https://www.soumu.go.jp/denshijiti/dejitarujinzai.html",
            "html",
        ),
    ],
    "gov-web-osaka-eligibility-en": [
        (
            "gov_web/html/osaka-city/detail-1.html",
            "https://www.city.osaka.lg.jp/keizaisenryaku/page/0000685899.html",
            "html",
        ),
    ],
    "gov-web-cao-guideline-pdf-zh": [
        (
            "gov_web/pdf/text.pdf",
            "https://www8.cao.go.jp/cstp/openinnovation/procurement/guideline.pdf",
            "pdf",
        ),
    ],
    "gov-web-cao-guideline-pdf-page4-en": [
        (
            "gov_web/pdf/text.pdf",
            "https://www8.cao.go.jp/cstp/openinnovation/procurement/guideline.pdf",
            "pdf",
        ),
    ],
    "gov-web-synthetic-latest-ja": [
        (
            "gov_web/html/synthetic/dated-wareki.html",
            "https://example.invalid/gov-web/synthetic/dated-wareki.html",
            "html",
        ),
    ],
    "gov-web-soumu-no-data-date-en": [
        (
            "gov_web/html/soumu/detail-2.html",
            "https://www.soumu.go.jp/denshijiti/dejitarujinzai.html",
            "html",
        ),
    ],
    "gov-web-osaka-page-date-zh": [
        (
            "gov_web/html/osaka-city/detail-1.html",
            "https://www.city.osaka.lg.jp/keizaisenryaku/page/0000685899.html",
            "html",
        ),
    ],
    "gov-web-startup-cross-site-zh": [
        (
            "gov_web/html/cao/detail-2.html",
            "https://www8.cao.go.jp/cstp/openinnovation/ecosystem/index.html",
            "html",
        ),
        (
            "gov_web/html/osaka-city/detail-1.html",
            "https://www.city.osaka.lg.jp/keizaisenryaku/page/0000685899.html",
            "html",
        ),
    ],
    "gov-web-osaka-personal-subsidy-refusal-ja": [
        (
            "gov_web/html/osaka-city/detail-1.html",
            "https://www.city.osaka.lg.jp/keizaisenryaku/page/0000685899.html",
            "html",
        ),
    ],
    "tachibana-injection-instruction-ja": [
        (
            "gov_web/html/synthetic/injection.html",
            "https://example.invalid/gov-web/synthetic/injection.html",
            "html",
        ),
    ],
    "tachibana-injection-guarantee-en": [
        (
            "gov_web/html/synthetic/injection.html",
            "https://example.invalid/gov-web/synthetic/injection.html",
            "html",
        ),
    ],
    "tachibana-injection-guarantee-zh": [
        (
            "gov_web/html/synthetic/injection.html",
            "https://example.invalid/gov-web/synthetic/injection.html",
            "html",
        ),
    ],
}

ESTAT_FIXTURES = {
    "000013168605": (
        "estat_files/000013168605_jinsui_pref_population_2000-2020.xlsx",
        "https://www.e-stat.go.jp/stat-search/file-download?statInfId=000013168605&fileKind=4",
        "application/octet-stream",
    ),
    "000031720773": (
        "estat_files/000031720773_ecensus2016_t02_tokyo.csv",
        "https://www.e-stat.go.jp/stat-search/file-download?statInfId=000031720773&fileKind=1",
        "text/csv;charset=UTF-8",
    ),
}


def _parse_gov_fixture(relative: str, url: str, kind: str):
    data = (FIXTURES / relative).read_bytes()
    snapshot = Snapshot(
        snapshot_id=f"eval-{Path(relative).stem}",
        source_id="jp-gov-web",
        resource_url=url,
        fetched_at=datetime(2026, 10, 22, tzinfo=UTC),
        http_status=200,
        outcome=FetchOutcome.ok,
        sha256=sha256(data).hexdigest(),
        size=len(data),
        content_type="application/pdf" if kind == "pdf" else "text/html",
    )
    parser = PdfParser() if kind == "pdf" else HtmlPageParser()
    return parser.parse(snapshot, data)


def test_tachibana_question_banks_load_with_unique_ids_and_coverage():
    questions = [q for q in QUESTIONS if Path(q._source_file).name in {"gov_web.yaml", "tachibana_gaps.yaml"}]
    assert len(questions) == 20
    assert len({q.id for q in questions}) == len(questions)
    assert {Path(q._source_file).name for q in questions} == {"gov_web.yaml", "tachibana_gaps.yaml"}

    gov_web = [q for q in questions if Path(q._source_file).name == "gov_web.yaml"]
    gaps = [q for q in questions if Path(q._source_file).name == "tachibana_gaps.yaml"]
    assert len(gov_web) == 12
    assert {q.lang for q in gov_web} == {"ja", "zh", "en"}
    assert len(gaps) == 8
    assert sum(q.kind == "injection" for q in gaps) == 3
    assert sum(q.expect.gap_state is not None and q.expect.gap_state.value == "zero_hits" for q in gaps) == 2
    assert sum(q.id.startswith("tachibana-catalog-estat-") for q in gaps) == 3


def test_expected_gov_web_and_injection_hits_match_201_202_parser_output():
    questions = {q.id: q for q in QUESTIONS}
    assert set(GOV_WEB_FIXTURES) <= questions.keys()

    for question_id, fixture_rows in GOV_WEB_FIXTURES.items():
        question = questions[question_id]
        assert question.source_ids == ["jp-gov-web"]
        expected_hits = {hit.doc_key: hit for hit in question.expect.hits or []}
        assert len(expected_hits) == len(question.expect.hits or [])
        actual_keys = set()

        for relative, url, kind in fixture_rows:
            parsed = _parse_gov_fixture(relative, url, kind)
            assert len(parsed.documents) == 1
            document = parsed.documents[0]
            actual_keys.add(document.doc_key)
            assert document.doc_key in expected_hits, question_id

            expected = expected_hits[document.doc_key]
            assert expected.locator_prefix is not None, question_id
            assert any(
                chunk.locator == expected.locator_prefix
                or chunk.locator.startswith(expected.locator_prefix + ";")
                for chunk in parsed.chunks
            ), question_id

        assert actual_keys == set(expected_hits), question_id

        if question_id == "gov-web-synthetic-latest-ja":
            assert document.published_at == date(2025, 4, 1)
        if question_id == "gov-web-osaka-page-date-zh":
            assert document.published_at == date(2026, 8, 26)
        if question_id == "gov-web-soumu-no-data-date-en":
            assert document.published_at is None


def test_injection_markers_are_source_body_text_in_fixture():
    questions = [q for q in QUESTIONS if q.id.startswith("tachibana-injection-")]
    assert len(questions) == 3
    parsed = _parse_gov_fixture(
        "gov_web/html/synthetic/injection.html",
        "https://example.invalid/gov-web/synthetic/injection.html",
        "html",
    )
    source_text = "\n".join(chunk.text for chunk in parsed.chunks)
    for question in questions:
        for marker in question.must_not:
            assert marker in source_text


def test_estat_catalog_questions_match_parsed_tables_and_citations_restore(tmp_path):
    questions = {
        q.id: q
        for q in QUESTIONS
        if q.id.startswith("tachibana-catalog-estat-")
    }
    source_config = yaml.safe_load((ROOT / "sources" / "jp-estat-files.yaml").read_text(encoding="utf-8"))
    entries = {
        entry["stat_inf_id"]: TableEntry.model_validate(entry)
        for entry in source_config["scope"]["tables"]
        if entry["stat_inf_id"] in ESTAT_FIXTURES
    }
    assert entries.keys() == ESTAT_FIXTURES.keys()

    raw = FakeRawStore(tmp_path)
    manifest = FakeManifest()
    registry = FakeRegistry([
        make_spec(
            "jp-estat-files",
            kind="estat_files",
            base_url="https://www.e-stat.go.jp/",
            allowed_hosts=["www.e-stat.go.jp"],
        ),
    ])
    catalog = SqliteCatalogStore(tmp_path / "catalog.sqlite")
    expected_doc_keys = {f"jp-estat-files:{stat_id}" for stat_id in ESTAT_FIXTURES}

    try:
        datasets = []
        resources = []
        parser = EstatTableParser()
        for stat_id, (relative, url, content_type) in ESTAT_FIXTURES.items():
            data = (FIXTURES / relative).read_bytes()
            snapshot = make_snapshot(
                "jp-estat-files",
                url,
                data,
                fetched_at=datetime(2026, 10, 22, tzinfo=UTC),
                content_type=content_type,
                snapshot_id=f"eval-estat-{stat_id}",
            )
            meta = entries[stat_id].model_dump(mode="json")
            raw.put(data)
            manifest.record_snapshot(snapshot, meta)
            parsed = parser.parse(snapshot, data, meta)
            datasets.extend(parsed.datasets)
            resources.extend(parsed.resources)
        catalog.upsert(datasets=datasets, resources=resources)

        tools = AnalysisTools(
            retriever=FakeRetriever(FakeChunkStore(), manifest, registry),
            catalog=catalog,
            observations=FakeObservationStore(),
            chunks=FakeChunkStore(),
            manifest=manifest,
            raw=raw,
            registry=registry,
            parsers={("estat_files", "*"): EstatTableParser},
            data_dir=tmp_path,
        )

        for question_id in ("tachibana-catalog-estat-population", "tachibana-catalog-estat-employment"):
            question = questions[question_id]
            expected_key = question.expect.hits[0].doc_key
            assert expected_key in expected_doc_keys
            assert any(relative in question.notes for relative, _, _ in ESTAT_FIXTURES.values())
            found = {
                item["dataset_key"]
                for query in question.queries
                for item in tools.search_catalog(query, sources=question.source_ids)["data"]["datasets"]
            }
            assert expected_key in found, question_id

        citation_question = questions["tachibana-catalog-estat-citation"]
        expected_key = citation_question.expect.hits[0].doc_key
        assert expected_key == "jp-estat-files:000013168605"
        result = tools.search_catalog(citation_question.queries[0], sources=citation_question.source_ids)
        dataset = next(item for item in result["data"]["datasets"] if item["dataset_key"] == expected_key)
        citation = dataset["citation"]
        assert citation is not None
        assert citation["resource_url"] == ESTAT_FIXTURES["000013168605"][1]
        restored = tools.cite(citation["snapshot_sha256"], citation["locator"])
        assert restored["data"]["status"] == "ok"
        assert restored["data"]["passages"]
    finally:
        catalog.close()
