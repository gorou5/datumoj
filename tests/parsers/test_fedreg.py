"""Recorded Federal Register sections and hostile input checks."""

import json
from datetime import date
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError, discover_parsers
from datumoj.rikolto.core.models import parse_locator
from datumoj.rikolto.parsers.fedreg import MAX_CHUNK_CHARS, MAX_XML_BYTES, FedregParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures/fedreg"
DETAIL = json.loads((FIXTURES / "document_2026-20122.json").read_bytes())
XML = (FIXTURES / "full_text_2026-20122.xml").read_bytes()


def sample(data=XML, meta=None):
    return ParseSample(make_snapshot("us-fedreg", DETAIL["full_text_xml_url"], data), data, DETAIL if meta is None else meta)


class TestFedregParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return FedregParser()

    @pytest.fixture
    def samples(self):
        return [sample()]


def test_recorded_section_count_path_date_and_document_number():
    s = sample()
    result = FedregParser().parse(s.snapshot, s.data, s.meta)
    document, = result.documents
    assert document.doc_key == "us-fedreg:2026-20122"
    assert document.published_at == date(2026, 10, 1)
    assert document.metadata["document_number"] == "2026-20122"
    assert document.metadata["html_url"] == DETAIL["html_url"]
    assert json.loads(document.metadata["agencies"]) == DETAIL["agencies"]
    assert json.loads(document.metadata["cfr_references"]) == DETAIL["cfr_references"]
    assert len(result.chunks) == 22
    assert parse_locator(result.chunks[0].locator)["section"] == "AGENCY"
    assert result.chunks[0].title_path == ["AGENCY"]
    assert all(c.published_at == document.published_at for c in result.chunks)
    background, = [c for c in result.chunks if c.title_path[-1] == "II. Background and Authority"]
    assert parse_locator(background.locator)["section"] == "SUPPLEMENTARY INFORMATION > II. Background and Authority"
    assert "COTP" in background.text


@pytest.mark.parametrize("filename", ["documents_rule_gte2026-10-01_p1.json", "document_2026-20122.json"])
def test_discovery_json_has_no_duplicate_documents_or_chunks(filename):
    data = (FIXTURES / filename).read_bytes()
    s = sample(data)
    result = FedregParser().parse(s.snapshot, data, s.meta)
    assert not result.documents and not result.chunks


def test_long_sections_split_at_paragraphs_without_crossing_headings():
    paragraphs = ["A " * 600, "B " * 600, "C " * 1200]
    data = ("<NOTICE><SUPLINF><HD SOURCE='HED'>SUPPLEMENTARY INFORMATION:</HD>"
            "<HD SOURCE='HD1'>II. Background</HD>" + "".join(f"<P>{p}</P>" for p in paragraphs)
            + "<HD SOURCE='HD1'>III. Next</HD><P>Next section.</P></SUPLINF></NOTICE>").encode()
    s = sample(data)
    chunks = FedregParser().parse(s.snapshot, data, s.meta).chunks
    assert len(chunks) == 4
    assert [c.text for c in chunks[:3]] == [p.strip() for p in paragraphs]
    assert all(c.title_path == ["SUPPLEMENTARY INFORMATION", "II. Background"] for c in chunks[:3])
    assert len(chunks[2].text) > MAX_CHUNK_CHARS  # An indivisible paragraph stays intact.
    assert chunks[3].title_path[-1] == "III. Next"


@pytest.mark.parametrize("data", [
    b'<!DOCTYPE RULE [<!ENTITY x "boom">]><RULE><P>&x;</P></RULE>',
    '<!DOCTYPE RULE><RULE/>'.encode("utf-16"),
    b"<RULE><P>broken", b"<PRESDOCU><P>Excluded</P></PRESDOCU>",
    b"{}", b" " * (MAX_XML_BYTES + 1),
])
def test_unsafe_invalid_or_excluded_input_raises_parse_error(data):
    s = sample(data)
    with pytest.raises(ParseError):
        FedregParser().parse(s.snapshot, data, s.meta)


def test_missing_date_is_not_replaced_by_fetch_date_and_wrong_number_is_rejected():
    s = sample(meta={**DETAIL, "publication_date": None})
    doc, = FedregParser().parse(s.snapshot, XML, s.meta).documents
    assert doc.published_at is None and doc.metadata["time_basis"] == "no_data_date"
    with pytest.raises(ParseError, match="number mismatch"):
        FedregParser().parse(s.snapshot, XML, {**DETAIL, "document_number": "2026-99999"})
    with pytest.raises(ParseError, match="publication_date"):
        FedregParser().parse(s.snapshot, XML, {**DETAIL, "publication_date": "invalid"})


def test_plugin_discovery_and_header_independence():
    assert discover_parsers()[("fedreg", "*")] is FedregParser
    s = sample()
    snapshot = s.snapshot.model_copy(update={"content_type": "text/html; charset=cp932"})
    assert len(FedregParser().parse(snapshot, XML, s.meta).chunks) == 22
