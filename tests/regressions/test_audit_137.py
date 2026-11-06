"""Dirty catalog entries stay local to the entry; no network or mutable fixtures."""

import base64
import json
from decimal import Decimal
from time import monotonic

import pytest

from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.parsers.ckan_meta import CkanMetaParser
from datumoj.rikolto.parsers.data_europa import DataEuropaParser
from datumoj.rikolto.parsers.estat_api import EstatAPIParser
from datumoj.rikolto.parsers.xml_law import MAX_XML_BYTES, LawXMLParser
from datumoj.rikolto.testing.samples import make_snapshot


def _parse(parser, source, payload, meta=None):
    data = json.dumps(payload, ensure_ascii=False).encode()
    snapshot = make_snapshot(source, "https://example.org/catalog", data)
    result = parser.parse(snapshot, data, meta)
    result.check_against(snapshot)
    return result


def _table(identifier="0004021108", description="unchanged"):
    return {"@id": identifier, "STAT_NAME": {"@code": "123", "$": "survey"},
            "TITLE": {"$": identifier}, "GOV_ORG": {"$": "publisher"},
            "OPEN_DATE": "2026-11-06", "DESCRIPTION": description}


def _estat_data(units):
    rows = [{"@time": f"202600{month:02}{month:02}", "@unit": unit, "$": str(month)}
            for month, unit in enumerate(units, 1)]
    return {"GET_STATS_DATA": {"RESULT": {"STATUS": 0}, "STATISTICAL_DATA": {
        "TABLE_INF": _table(),
        "RESULT_INF": {"TOTAL_NUMBER": len(rows), "FROM_NUMBER": 1, "TO_NUMBER": len(rows)},
        "CLASS_INF": {"CLASS_OBJ": {"@id": "time", "CLASS": [
            {"@code": row["@time"], "@name": row["@time"]} for row in rows]}},
        "DATA_INF": {"VALUE": rows},
    }}}


@pytest.mark.parametrize(("units", "configured"), [(["％", "%"], "%"), (["%", "％"], "％")])
def test_estat_nfkc_unit_keeps_all_values_and_configured_unit(units, configured):
    result = _parse(EstatAPIParser(), "jp-estat-api", _estat_data(units),
                    {"stats_data_id": "0004021108", "unit": configured})
    assert [obs.value for obs in result.observations] == [Decimal(1), Decimal(2)]
    assert [obs.unit for obs in result.observations] == [configured, configured]
    assert len(result.datasets) == 1


@pytest.mark.parametrize("unit", ["人", "千人", "percentage", "‰", "percent"])
def test_estat_different_unit_still_fails(unit):
    with pytest.raises(ParseError, match="unit mismatch"):
        _parse(EstatAPIParser(), "jp-estat-api", _estat_data(["％", unit]),
               {"stats_data_id": "0004021108", "unit": "%"})


def test_estat_catalog_keeps_first_duplicate_and_original_row_locators():
    first = _table(description={"TABULATION_SUB_CATEGORY1": "first", "ignored": 7,
                                "TABULATION_SUB_CATEGORY2": {"$": "second"}})
    payload = {"GET_STATS_LIST": {"RESULT": {"STATUS": 0}, "DATALIST_INF": {"TABLE_INF": [
        first, _table(description="duplicate"), _table("other", "original text"),
        {"@id": "0004021108"},  # A duplicate is discarded before its other fields are inspected.
    ]}}}
    result = _parse(EstatAPIParser(), "jp-estat-api", payload)
    assert [d.dataset_key for d in result.datasets] == ["jp-estat-api:0004021108", "jp-estat-api:other"]
    assert [d.description for d in result.datasets] == ["first\nsecond", "original text"]
    assert [d.locator for d in result.datasets] == ["row=1", "row=3"]
    assert len(result.warnings) == 2
    assert all("0004021108" in warning and "skipped" in warning for warning in result.warnings)


@pytest.mark.parametrize("description", [None, 12, True, [], ["not a description"], {}, {"number": 12}])
def test_estat_nontext_description_does_not_break_other_tables(description):
    payload = {"GET_STATS_LIST": {"RESULT": {"STATUS": 0}, "DATALIST_INF": {"TABLE_INF": [
        _table(description=description), _table("other"),
    ]}}}
    result = _parse(EstatAPIParser(), "jp-estat-api", payload)
    assert [d.description for d in result.datasets] == [None, "unchanged"]


@pytest.mark.parametrize("bad_url", ['=HYPERLINK("#", "https://example.org/a")',
                                     "file:///usr/verticles/dataset", "ftp://example.org/data",
                                     "https://", "relative/path", 17, {"url": "https://example.org"}])
@pytest.mark.parametrize("landing_page", ["https://example.org/fallback?token=REDACTED", "file:///invalid", None])
def test_ckan_invalid_package_url_matches_missing_url_and_keeps_resources(bad_url, landing_page):
    package = {"id": "dirty", "title": "preserved", "notes": "description", "license_id": "CC-BY",
               "landingPage": landing_page,
               "resources": [{"id": "a", "url": "https://example.org/a.csv"},
                             {"id": "b", "url": "https://example.org/b.csv"}]}
    missing = _parse(CkanMetaParser(), "jp-bodik", package)
    invalid = _parse(CkanMetaParser(), "jp-bodik", {**package, "url": bad_url})
    assert invalid.datasets == missing.datasets
    assert invalid.resources == missing.resources
    assert invalid.datasets[0].url == (landing_page if landing_page and landing_page.startswith("https://") else None)
    assert len(invalid.resources) == 2
    assert len(invalid.warnings) == len(missing.warnings) + 1
    assert "url" in invalid.warnings[0]
    if landing_page == "file:///invalid":
        assert "landingPage" in invalid.warnings[1]


def test_ckan_other_invalid_fields_still_raise():
    with pytest.raises(ParseError, match="CKAN package 字段无效"):
        _parse(CkanMetaParser(), "jp-bodik", {"id": "bad", "title": " ", "url": "file:///invalid"})
    with pytest.raises(ParseError, match="resources 必须是数组"):
        _parse(CkanMetaParser(), "jp-bodik", {"id": "bad", "resources": {}})


def test_valid_long_urls_remain_unchanged():
    url = "https://example.org/" + "a" * 2100
    ckan = _parse(CkanMetaParser(), "jp-bodik", {"id": "good", "url": url})
    europa = _parse(DataEuropaParser(), "eu-data-europa", {"@graph": [
        {"@type": "dcat:Dataset", "dct:identifier": "good", "dcat:landingPage": {"@id": url},
         "dcat:distribution": {"@id": "urn:resource"}},
        {"@id": "urn:resource", "dcat:downloadURL": {"@id": url}},
    ]})
    assert ckan.datasets[0].url == europa.datasets[0].url == europa.resources[0].url == url
    assert not ckan.warnings and not europa.warnings


@pytest.mark.parametrize("landing_page", ["file:///usr/verticles/dataset", "https://example.org/dataset", None])
def test_europa_skips_each_bad_distribution_and_keeps_dataset_and_good_resources(landing_page):
    record = {"@id": "urn:dataset", "@type": "dcat:Dataset", "dct:identifier": "nuts-2010-03m",
              "dct:title": {"@value": "preserved", "@language": "en"},
              "dcat:landingPage": {"@id": landing_page},
              "dcat:distribution": [
                  {"@id": "urn:good-download"}, {}, {"@id": "urn:no-url"},
                  {"@id": "urn:file"}, {"@id": "urn:missing-node"},
                  {"@id": "urn:formula"}, {"@id": " "}, {"@id": "urn:good-access"},
              ]}
    payload = {"@graph": [record,
        {"@id": "urn:good-download", "dcat:downloadURL": {"@id": "https://example.org/a.csv?token=secret"}},
        {"@id": "urn:no-url"},
        {"@id": "urn:file", "dcat:accessURL": {"@id": "file:///usr/verticles/data.csv"}},
        {"@id": "urn:formula", "dcat:downloadURL": {"@id": '=HYPERLINK("#", "https://example.org/a")'}},
        {"@id": " ", "dcat:downloadURL": {"@id": "https://example.org/blank-id.csv"}},
        {"@id": "urn:good-access", "dcat:accessURL": {"@id": "http://example.org/b.csv"}},
    ]}
    result = _parse(DataEuropaParser(), "eu-data-europa", payload)
    assert len(result.datasets) == 1
    assert result.datasets[0].title == "preserved"
    assert result.datasets[0].url == (landing_page if landing_page and landing_page.startswith("https://") else None)
    assert [r.resource_id for r in result.resources] == ["urn:good-download", "urn:good-access"]
    assert [r.url for r in result.resources] == ["https://example.org/a.csv?token=REDACTED", "http://example.org/b.csv"]
    for index in range(1, 7):
        assert any(f"distribution[{index}]" in warning and "skipped" in warning for warning in result.warnings)
    assert len(result.warnings) == 6 + (landing_page is not None and landing_page.startswith("file:"))


def _large_law():
    # Many real article nodes exercise tree building and chunk creation, not just padding.
    articles = [f'<Article Num="{i}"><Paragraph><Sentence>{"text " * 300}</Sentence></Paragraph></Article>'.encode()
                for i in range(1, 15001)]
    data = (b"<Law><LawNum>synthetic</LawNum><LawBody><LawTitle>synthetic law</LawTitle><MainProvision>"
            + b"".join(articles) + b"</MainProvision></LawBody></Law>")
    assert 22 * 1024 * 1024 < len(data) < 23 * 1024 * 1024
    return data


@pytest.mark.parametrize("wrapped", [False, True], ids=["xml", "api-json"])
def test_law_22_mib_parses_within_60_seconds(wrapped):
    data = _large_law()
    if wrapped:
        data = json.dumps({"law_full_text": base64.b64encode(data).decode(),
                           "law_info": {"law_id": "synthetic"}}).encode()
    assert len(data) < MAX_XML_BYTES
    snapshot = make_snapshot("jp-egov-law", "https://example.org/law", data)
    started = monotonic()
    result = LawXMLParser().parse(snapshot, data, {"law_id": "synthetic"})
    elapsed = monotonic() - started
    print(f"law wrapped={wrapped}: {len(data)} bytes, {len(result.chunks)} chunks, {elapsed:.3f}s / 60s")
    assert elapsed < 60
    result.check_against(snapshot)
    assert len(result.documents) == 1
    assert len(result.chunks) == 15000
    assert result.chunks[0].locator == "art=1"
    assert result.chunks[-1].locator == "art=15000"


def test_law_32_mib_limit_is_still_enforced():
    assert MAX_XML_BYTES == 32 * 1024 * 1024
    data = b" " * (MAX_XML_BYTES + 1)
    snapshot = make_snapshot("jp-egov-law", "https://example.org/law", data)
    with pytest.raises(ParseError, match="大小上限"):
        LawXMLParser().parse(snapshot, data, {"law_id": "synthetic"})


def test_parser_versions_are_unchanged():
    assert EstatAPIParser.version == "1"
    assert CkanMetaParser.version == "2"
    assert DataEuropaParser.version == "1"
    assert LawXMLParser.version == "1"
