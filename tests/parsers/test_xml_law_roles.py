from pathlib import Path

import pytest

from datumoj.core.contracts import ParseError
from datumoj.parsers.xml_law import LawXMLParser
from datumoj.testing.samples import make_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures" / "egov_law"


def _sample(filename: str):
    data = (FIXTURES / filename).read_bytes()
    snapshot = make_snapshot(
        source_id="jp-egov-law",
        url=f"https://laws.e-gov.go.jp/api/2/{filename}",
        data=data,
        content_type="application/json",
    )
    return snapshot, data


@pytest.mark.parametrize(
    ("filename", "role"),
    [
        ("laws_list.limit10.offset0.json", "law_list"),
        ("law_revisions.415AC0000000057.json", "law_revisions"),
    ],
)
def test_non_body_roles_return_empty_result_with_warning(filename, role):
    snapshot, data = _sample(filename)

    result = LawXMLParser().parse(snapshot, data, {"role": role})

    assert result.documents == []
    assert result.chunks == []
    assert result.warnings == [f"非正文快照（role={role}），跳过"]


def test_law_data_role_still_parses_full_text():
    filename = "law_data.322AC0000000049.asof2026-10-20.xml-in-json.json"
    snapshot, data = _sample(filename)

    result = LawXMLParser().parse(
        snapshot,
        data,
        {"role": "law_data", "law_id": "322AC0000000049"},
    )

    assert [document.doc_key for document in result.documents] == ["jp-egov-law:322AC0000000049"]
    assert result.chunks
    assert result.warnings == []


@pytest.mark.parametrize("meta", [None, {"law_id": "322AC0000000049"}])
def test_missing_role_keeps_parse_error_behavior(meta):
    snapshot, _ = _sample("laws_list.limit10.offset0.json")

    with pytest.raises(ParseError):
        LawXMLParser().parse(snapshot, b"not a law response", meta)
