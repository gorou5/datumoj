import json
from datetime import date
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.parsers.xml_law import MAX_XML_BYTES, LawXMLParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures" / "egov_law"


def _sample(filename: str, law_id: str, pending_amendments=None) -> ParseSample:
    data = (FIXTURES / filename).read_bytes()
    return ParseSample(
        make_snapshot(
            source_id="jp-egov-law",
            url=f"https://laws.e-gov.go.jp/api/2/law_data/{law_id}",
            data=data,
            content_type="application/json",
        ),
        data,
        {
            "task": "law_data",
            "law_id": law_id,
            "asof": "2026-10-20",
            "pending_amendments": pending_amendments or [],
        },
    )


class TestLawXMLParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return LawXMLParser()

    @pytest.fixture
    def samples(self):
        return [
            _sample("law_data.322AC0000000049.asof2026-10-20.xml-in-json.json", "322AC0000000049"),
            _sample("law_data.425AC0000000027.asof2026-10-20.xml-in-json.json", "425AC0000000027"),
            _sample("law_data.415AC0000000057.asof2026-10-20.xml-in-json.json", "415AC0000000057"),
        ]

    @pytest.fixture
    def bad_samples(self):
        return [(FIXTURES / "law_full_text.synthetic.entity-attack.xml").read_bytes()]

    def test_articles_supplements_tables_and_hierarchy(self, parser, samples):
        result = parser.parse(samples[0].snapshot, samples[0].data, samples[0].meta)
        doc = result.documents[0]
        locators = {chunk.locator for chunk in result.chunks}

        assert doc.doc_key == "jp-egov-law:322AC0000000049"
        assert doc.title == "労働基準法"
        assert doc.metadata["pending_amendments"] == "[]"
        assert any(locator.startswith("art=") for locator in locators)
        assert any(locator.startswith("suppl=") for locator in locators)
        assert "table=1" in locators
        assert any("第一章" in title for chunk in result.chunks for title in chunk.title_path)
        assert all(chunk.effective_at == date(2026, 10, 20) for chunk in result.chunks)

    def test_subarticle_and_long_articles_split_by_paragraph_and_item(self, parser, samples):
        law = samples[1]
        result = parser.parse(law.snapshot, law.data, law.meta)
        locators = {chunk.locator for chunk in result.chunks}

        assert any(locator.endswith("art=3_2") for locator in locators)
        assert "art=19;para=1" not in locators
        assert any(locator.startswith("art=19;para=1;item=") for locator in locators)
        assert all(len(chunk.text) <= 2000 for chunk in result.chunks if chunk.locator.startswith("art=19;"))

    def test_law_metadata_asof_pending_and_snapshot_stable_ids(self, parser, samples):
        sample = samples[2]
        meta = {
            **sample.meta,
            "pending_amendments": [
                {
                    "amendment_law_num": "令和八年法律第十号",
                    "promulgate_date": "2026-04-01",
                    "enforcement_date": "2027-04-01",
                    "enforcement_comment": None,
                }
            ],
        }
        first = parser.parse(sample.snapshot, sample.data, meta)
        doc = first.documents[0]
        assert doc.metadata["law_num"] == "平成十五年法律第五十七号"
        assert doc.metadata["law_title"] == "個人情報の保護に関する法律"
        assert doc.metadata["asof"] == "2026-10-20"
        assert "令和八年法律第十号" in doc.metadata["pending_amendments"]
        assert all(chunk.effective_at == date(2026, 10, 20) for chunk in first.chunks)

        later_snapshot = make_snapshot(
            source_id="jp-egov-law",
            url=sample.snapshot.resource_url,
            data=sample.data + b" ",
            snapshot_id="same-law-later-snapshot",
        )
        second = parser.parse(later_snapshot, sample.data + b" ", meta)
        assert [chunk.chunk_id for chunk in first.chunks] == [chunk.chunk_id for chunk in second.chunks]

    def test_missing_asof_does_not_invent_effective_date(self, parser, samples):
        sample = samples[0]
        meta = {key: value for key, value in sample.meta.items() if key != "asof"}
        result = parser.parse(sample.snapshot, sample.data, meta)
        assert "asof" not in result.documents[0].metadata
        assert result.documents[0].effective_at is None
        assert all(chunk.effective_at is None for chunk in result.chunks)

    def test_entity_xml_is_rejected_in_direct_and_api_payload_forms(self, parser, samples):
        evil_xml = (FIXTURES / "law_full_text.synthetic.entity-attack.xml").read_bytes()
        evil_payload = json.loads((FIXTURES / "law_data.synthetic.entity-attack.json").read_bytes())
        snapshot = samples[0].snapshot
        with pytest.raises(ParseError):
            parser.parse(snapshot, evil_xml, {"law_id": "428AC1000000103"})
        with pytest.raises(ParseError):
            parser.parse(
                snapshot,
                json.dumps(evil_payload).encode(),
                {"law_id": "428AC1000000103"},
            )

    def test_input_size_is_limited(self, parser, samples):
        sample = samples[0]
        data = b" " * (MAX_XML_BYTES + 1)
        oversized = make_snapshot(
            source_id="jp-egov-law",
            url=sample.snapshot.resource_url,
            data=data,
            snapshot_id="oversized-law",
        )
        with pytest.raises(ParseError, match="大小上限"):
            parser.parse(oversized, data, sample.meta)
