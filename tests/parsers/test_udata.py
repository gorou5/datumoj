import json
from datetime import datetime
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import discover_parsers
from datumoj.rikolto.parsers.udata import UdataParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

FIX = Path(__file__).parents[1] / "fixtures/udata_fr"


class TestUdataParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return UdataParser()

    @pytest.fixture
    def samples(self):
        bodies = [(FIX / f"datasets_org-insee_page{i}_size2.json").read_bytes() for i in (1, 2)]
        return [ParseSample(make_snapshot("fr-data-gouv", data=body), body) for body in bodies]

    def test_fields_and_privacy(self, parser, samples):
        assert discover_parsers()[("udata", "*")] is UdataParser
        count = 0
        for sample in samples:
            result = parser.parse(sample.snapshot, sample.data)
            records = json.loads(sample.data)["data"]
            count += len(result.datasets)
            for index, (dataset, record) in enumerate(zip(result.datasets, records)):
                assert dataset.dataset_key == f"fr-data-gouv:{record['id']}"
                assert dataset.modified_at == datetime.fromisoformat(record["last_modified"])
                assert dataset.license == record["license"]
                assert dataset.locator == f"entry={index}"
                assert dataset.extras["temporal_coverage"] == record["temporal_coverage"]
            expected = [r["url"] for d in records for r in d["resources"]]
            assert [r.url for r in result.resources] == expected
            assert "support-apim@insee.fr" not in result.model_dump_json()
        assert count == 4
