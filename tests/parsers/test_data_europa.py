from pathlib import Path

import pytest

from datumoj.rikolto.parsers.data_europa import DataEuropaParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

DATA = (Path(__file__).parents[1] / "fixtures/data_europa/repo_dataset_combined-nomenclature-2023.jsonld").read_bytes()
URL = "https://data.europa.eu/api/hub/repo/datasets/combined-nomenclature-2023"


class TestParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return DataEuropaParser()

    @pytest.fixture
    def samples(self):
        return [ParseSample(make_snapshot("eu-data-europa", URL, DATA), DATA,
                            {"role": "dataset", "dataset_id": "combined-nomenclature-2023"})]

    def test_multilingual_and_distributions(self, parser, samples):
        sample = samples[0]
        result = parser.parse(sample.snapshot, sample.data, sample.meta)
        dataset = result.datasets[0]
        assert dataset.title.startswith("Combined Nomenclature")
        assert {x["@language"] for x in dataset.extras["titles"]} >= {"en", "fr-t-en-t0-mtec", "de-t-en-t0-mtec"}
        assert len(result.resources) == 4
        assert all(r.license == "http://publications.europa.eu/resource/authority/licence/COM_REUSE" for r in result.resources)
