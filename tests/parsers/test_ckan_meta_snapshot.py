import json
from pathlib import Path

import pytest

from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot
from datumoj.rikolto.wiring import _CkanPages

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ckan_egov"
SOURCE_ID = "jp-ckan-egov"
SEARCH_URL = "https://data.e-gov.go.jp/data/api/action/package_search?q=%E4%BA%BA%E5%8F%A3"


class TestCkanPagesParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return _CkanPages()

    @pytest.fixture
    def samples(self):
        data = (FIXTURES / "package_search_q-jinko_rows5_start0.json").read_bytes()
        return [ParseSample(
            snapshot=make_snapshot(
                SOURCE_ID, SEARCH_URL, data, content_type="application/json",
            ),
            data=data,
            meta={"role": "package_search"},
        )]

    def test_page_datasets_point_to_their_raw_result_entries(self, parser, samples):
        sample = samples[0]
        result = parser.parse(sample.snapshot, sample.data, sample.meta)

        assert parser.version == "2"
        assert len(result.datasets) == 5
        assert [dataset.locator for dataset in result.datasets] == [f"entry={i}" for i in range(5)]
        assert all(dataset.snapshot_sha256 == sample.snapshot.sha256 for dataset in result.datasets)


@pytest.mark.parametrize("landing_page", ["https://data.bodik.jp/dataset/dirty", 'file:///invalid'])
def test_invalid_package_url_uses_landing_page_and_keeps_the_rest_of_the_page(landing_page):
    bad = {"id": "dirty", "url": '=HYPERLINK("#", "https://example.org/a")',
           "landingPage": landing_page,
           "resources": [{"id": "dirty-resource", "url": "https://example.org/data.csv"}]}
    good = {"id": "good", "url": "https://example.org/good"}
    data = json.dumps({"success": True, "result": {"results": [bad, good]}}).encode()
    snapshot = make_snapshot(SOURCE_ID, SEARCH_URL, data)
    result = _CkanPages().parse(snapshot, data, {"role": "package_search"})

    assert [d.dataset_key for d in result.datasets] == [f"{SOURCE_ID}:dirty", f"{SOURCE_ID}:good"]
    assert [d.url for d in result.datasets] == [landing_page if landing_page.startswith("https://") else None,
                                             "https://example.org/good"]
    assert [d.locator for d in result.datasets] == ["entry=0", "entry=1"]
    assert all(d.snapshot_sha256 == snapshot.sha256 for d in result.datasets)
    assert [r.resource_id for r in result.resources] == ["dirty-resource"]
    assert len(result.warnings) == (1 if landing_page.startswith("https://") else 2)
