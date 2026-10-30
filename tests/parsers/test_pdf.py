from __future__ import annotations

from datetime import date
from hashlib import sha1
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.parsers.pdf import PdfParser, _title
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures" / "gov_web"
PDF_FIXTURES = FIXTURES / "pdf"
TEXT_URL = "https://www8.cao.go.jp/cstp/openinnovation/procurement/guideline.pdf"
PARENT_URL = "https://www8.cao.go.jp/cstp/openinnovation/procurement/index.html"
PARENT_SHA256 = "09d80e08ea608dde87780e39977b1cd1056d66075210172ce1b792e2e88aa4f8"


def _sample(filename: str, url: str = TEXT_URL, meta: dict | None = None) -> ParseSample:
    data = (PDF_FIXTURES / filename).read_bytes()
    return ParseSample(
        make_snapshot(source_id="jp-cao", url=url, data=data, content_type="application/pdf"),
        data,
        {} if meta is None else meta,
    )


class TestPdfParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return PdfParser()

    @pytest.fixture
    def samples(self):
        return [_sample("text.pdf", meta={"parent_url": PARENT_URL, "parent_sha256": PARENT_SHA256})]

    @pytest.fixture
    def bad_samples(self):
        return [(PDF_FIXTURES / name).read_bytes() for name in ("encrypted.pdf", "broken.pdf")]

    def test_declarations_and_discovery(self, parser):
        assert parser.source_kind == "gov_web"
        assert parser.content_type == "application/pdf"
        assert parser.name and parser.version

    def test_text_pdf_has_page_chunks_and_stable_document_metadata(self, parser, samples):
        sample = samples[0]
        result = parser.parse(sample.snapshot, sample.data, {**sample.meta, "published_at": "2019-06-21"})
        document = result.documents[0]

        expected_id = sha1(sample.snapshot.resource_url.encode("utf-8")).hexdigest()[:16]
        assert document.doc_key == f"jp-cao:pdf:{expected_id}"
        assert document.kind == "pdf"
        assert document.content_type == "application/pdf"
        assert document.title.startswith("大学・国立研究開発法人の外国企業との連携")
        assert document.metadata["pages"] == "36"
        assert document.metadata["parent_url"] == PARENT_URL
        assert document.metadata["parent_sha256"] == PARENT_SHA256
        assert document.metadata["published_at"] == "2019-06-21"
        assert document.published_at == date(2019, 6, 21)
        assert document.metadata["needs_ocr_pages"] == "1"
        assert len(result.chunks) == 35
        assert "needs_ocr page=25" in result.warnings
        assert all(chunk.locator.startswith("page=") for chunk in result.chunks)
        page_one = next(chunk for chunk in result.chunks if chunk.locator == "page=1")
        assert "大学・国立研究開発法人の外国企業との連携" in page_one.text
        assert "\n" not in page_one.text

        equivalent_url = "HTTPS://WWW8.CAO.GO.JP:443/cstp/openinnovation/procurement/guideline.pdf#page=1"
        equivalent = make_snapshot(
            source_id="jp-cao", url=equivalent_url, data=sample.data, content_type="application/pdf"
        )
        assert parser.parse(equivalent, sample.data).documents[0].doc_key == document.doc_key

    def test_scanned_pdf_is_kept_without_chunks_and_marked_for_ocr(self, parser):
        sample = _sample("scanned.pdf")
        result = parser.parse(sample.snapshot, sample.data)

        assert len(result.documents) == 1
        assert result.chunks == []
        assert result.documents[0].metadata["pages"] == "2"
        assert result.documents[0].metadata["needs_ocr_pages"] == "2"
        assert "needs_ocr page=1" in result.warnings
        assert "needs_ocr page=2" in result.warnings
        assert "needs_ocr all" in result.warnings

    def test_page_heading_and_filename_title_fallbacks(self, parser):
        guide = (FIXTURES / "html/synthetic/site/policy/startup/guide.pdf").read_bytes()
        snapshot = make_snapshot(
            source_id="jp-cao",
            url="https://example.go.jp/policy/guide.pdf",
            data=guide,
            content_type="application/pdf",
        )
        result = parser.parse(snapshot, guide)
        assert result.documents[0].title == "Sogyo guidebook - DATUMOJ synthetic fixture"
        assert result.documents[0].published_at is None
        assert "published_at" not in result.documents[0].metadata
        assert _title({"Title": "\ufffd\ufffd"}, "", "https://example.go.jp/reports/final%20draft.pdf") == "final draft.pdf"

    def test_encrypted_pdf_raises_parse_error_without_password_attempt(self, parser):
        sample = _sample("encrypted.pdf")
        with pytest.raises(ParseError, match="encrypted"):
            parser.parse(sample.snapshot, sample.data)

    def test_truncated_pdf_raises_parse_error(self, parser):
        sample = _sample("broken.pdf")
        with pytest.raises(ParseError):
            parser.parse(sample.snapshot, sample.data)
