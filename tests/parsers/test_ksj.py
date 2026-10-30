import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.core.models import parse_locator
from datumoj.rikolto.parsers.ksj import KsjParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
DATA = (ROOT / "tests/fixtures/ksj/P05-22_41_GML.zip").read_bytes()
URL = "https://nlftp.mlit.go.jp/ksj/gml/data/P05/P05-22/P05-22_41_GML.zip"


def sample(data=DATA):
    return ParseSample(make_snapshot("jp-ksj", URL, data, content_type="text/html; charset=UTF-8"), data)


class TestKsjParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return KsjParser()

    @pytest.fixture
    def samples(self):
        return [sample()]

    @pytest.fixture
    def bad_samples(self):
        return [b"not a ZIP", DATA[:100]]


def test_real_municipalities_facilities_and_mixed_encoding():
    s = sample()
    result = KsjParser().parse(s.snapshot, s.data)
    dataset, = result.datasets
    assert dataset.title == "国土数値情報（市町村役場・公的集会施設）データ 2022年度 佐賀県"
    assert (dataset.publisher, dataset.license, dataset.extras["year"]) == ("国土交通省", "CC BY 4.0", 2022)
    assert dataset.locator == "member=KS-META-P05-22_41.xml"
    facilities = [c for c in result.chunks if "feature" in parse_locator(c.locator)]
    assert len(facilities) == 20 and dataset.extras["feature_count"] == 636
    assert next(c for c in result.chunks if c.locator == dataset.locator).text == dataset.title
    saga = next(c for c in result.chunks if c.chunk_id.endswith(":41201"))
    assert all(value in saga.text for value in ("佐賀市役所", "嘉瀬公民館", "佐賀市嘉瀬町中原1690", "130.2612733", "33.23781291"))
    locator = parse_locator(saga.locator)
    assert locator["member"] == "P05-22_41.geojson"
    assert len(locator["feature"].split(",")) == 96
    with ZipFile(io.BytesIO(DATA)) as z:
        features = json.loads(z.read(locator["member"]))["features"]
    for chunk in facilities:
        indices = [int(i) for i in parse_locator(chunk.locator)["feature"].split(",")]
        for i in indices:
            props = features[i - 1]["properties"]
            assert props["P05_001"] == chunk.chunk_id.rsplit(":", 1)[1]
            assert props["P05_003"] in chunk.text and props["P05_004"] in chunk.text
    assert result.documents[0].published_at is None  # A data edition is not a publication date.


@pytest.mark.parametrize("change", ["encoding", "entities", "coordinates", "wrong_area", "missing_member"])
def test_invalid_members_fail_as_a_whole(tmp_path, change):
    with ZipFile(io.BytesIO(DATA)) as z:
        members = {n: z.read(n) for n in z.namelist()}
    if change == "encoding":
        members["KS-META-P05-22_41.xml"] = b"\x81"
    elif change == "entities":
        members["KS-META-P05-22_41.xml"] = b'<!DOCTYPE x [<!ENTITY x "payload">]><x>&x;</x>'
    elif change == "missing_member":
        del members["P05-22_41.geojson"]
    else:
        geo = json.loads(members["P05-22_41.geojson"])
        if change == "coordinates":
            geo["features"][0]["geometry"]["coordinates"] = [float("nan"), 33]
        else:
            geo["features"][0]["properties"]["P05_001"] = "13201"
        members["P05-22_41.geojson"] = json.dumps(geo).encode()
    path = tmp_path / "invalid.synthetic.zip"
    with ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    s = sample(path.read_bytes())
    with pytest.raises(ParseError):
        KsjParser().parse(s.snapshot, s.data)
