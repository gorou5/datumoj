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
