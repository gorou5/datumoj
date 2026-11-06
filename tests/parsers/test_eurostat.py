from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from datumoj.rikolto.core.contracts import ParseError, discover_parsers
from datumoj.rikolto.core.models import TimeGranularity
from datumoj.rikolto.parsers.eurostat import EurostatParser, _period, read_structure
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/eurostat"
BASE = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/"
GOV = "gov_10dd_edpt1"
GDP = "nama_10_gdp"
POP = "demo_pjan"
NS = {"m": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
      "s": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
      "c": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
      "g": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic"}


def dsd_bytes(code, *, labels=False):
    """Captured DSDs; population and optional labels are explicitly synthetic."""
    if code == POP:
        return (f'<m:Structure xmlns:m="{NS["m"]}" xmlns:s="{NS["s"]}" xmlns:c="{NS["c"]}">'
                '<m:Structures><s:DataStructures><s:DataStructure id="DEMO_PJAN">'
                '<c:Name xml:lang="en">Synthetic population on 1 January</c:Name>'
                '<c:Description>Synthetic population DSD</c:Description><s:DataStructureComponents>'
                '<s:DimensionList>' + ''.join(f'<s:Dimension id="{dim}"/>' for dim in ("freq", "unit", "age", "sex", "geo"))
                + '</s:DimensionList></s:DataStructureComponents></s:DataStructure>'
                '</s:DataStructures></m:Structures></m:Structure>').encode()
    data = (FIXTURES / f"datastructure_{code.upper()}.xml").read_bytes()
    if not labels:
        return data
    root = etree.fromstring(data)
    lists = etree.SubElement(root.find("m:Structures", NS), f'{{{NS["s"]}}}Codelists')
    for name, codes in {"GEO": {"EL": "Synthetic Greece"}, "UNIT": {"PC_GDP": "Synthetic percent of GDP", "MIO_EUR": "Synthetic million euro"}}.items():
        codelist = etree.SubElement(lists, f'{{{NS["s"]}}}Codelist', id=name)
        for key, label in codes.items():
            item = etree.SubElement(codelist, f'{{{NS["s"]}}}Code', id=key)
            etree.SubElement(item, f'{{{NS["c"]}}}Name', {"{http://www.w3.org/XML/1998/namespace}lang": "en"}).text = label
    return etree.tostring(root)


def data_bytes(code):
    if code == POP:
        # Synthetic response: only tests the third dataset's distinct dimension set.
        return (f'<m:GenericData xmlns:m="{NS["m"]}" xmlns:g="{NS["g"]}">'
                '<m:Header><m:DataSetID>DEMO_PJAN</m:DataSetID></m:Header><m:DataSet><g:Series><g:SeriesKey>'
                '<g:Value id="freq" value="A"/><g:Value id="unit" value="NR"/>'
                '<g:Value id="age" value="TOTAL"/><g:Value id="sex" value="T"/><g:Value id="geo" value="DE"/>'
                '</g:SeriesKey><g:Obs><g:ObsDimension value="2020"/><g:ObsValue value="123"/>'
                '</g:Obs></g:Series></m:DataSet></m:GenericData>').encode()
    file = "gov_10dd_edpt1_EL_2000-2015.xml" if code == GOV else "nama_10_gdp_DE-FR-NL_2020-2024.xml"
    return (FIXTURES / file).read_bytes()


def sample(code=GOV, data=None, *, labels=False):
    data = data if data is not None else data_bytes(code)
    return ParseSample(make_snapshot("eu-eurostat", BASE + "data/" + code, data), data,
                       {"id": code, "structure": read_structure(dsd_bytes(code, labels=labels), code)})


def parse(item):
    return EurostatParser().parse(item.snapshot, item.data, item.meta)


class TestEurostatParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return EurostatParser()

    @pytest.fixture
    def samples(self):
        result = [sample(GOV), sample(GDP), sample(POP)]
        for code in (GOV, GDP):
            data = dsd_bytes(code)
            result.append(ParseSample(make_snapshot("eu-eurostat", BASE + "datastructure/ESTAT/" + code.upper(), data), data, {"id": code}))
        return result

    @pytest.fixture
    def bad_samples(self):
        return [b"not xml", b"<html/>", b'<m:Structure xmlns:m="' + NS["m"].encode() + b'"/>',
                b'<!DOCTYPE x [<!ENTITY e "unsafe">]><x>&e;</x>']


def test_recorded_greece_units_dates_and_dsd_labels():
    assert discover_parsers()[("eurostat_sdmx", "*")] is EurostatParser
    parsed = parse(sample(labels=True))
    assert len(parsed.observations) == 64
    rows = [o for o in parsed.observations if o.time_label == "2009" and o.indicator == "B9"]
    assert {o.unit: o.value for o in rows} == {"PC_GDP": Decimal("-15.4"), "MIO_EUR": Decimal("-36141.0")}
    deficit = next(o for o in rows if o.unit == "PC_GDP")
    assert deficit.area_code == "EL" and deficit.area_label == "Synthetic Greece"
    assert deficit.dims == {"freq": "A", "unit": "PC_GDP", "sector": "S13", "na_item": "B9", "geo": "EL", "TIME_PERIOD": "2009"}
    assert (deficit.time_start, deficit.time_end, deficit.granularity) == (date(2009, 1, 1), date(2009, 12, 31), TimeGranularity.year)
    assert all(o.release_id is None and o.released_at is None for o in parsed.observations)
    dataset = parsed.datasets[0]
    assert dataset.title == "GOV_10DD_EDPT1 data structure"
    assert dataset.extras["codelists"]["unit"]["PC_GDP"] == "Synthetic percent of GDP"
    assert dataset.extras["codelists"]["unit"]["MIO_EUR"] == "Synthetic million euro"
    assert not parsed.warnings


def test_recorded_flags_and_absent_codelists_are_not_invented():
    parsed = parse(sample(GDP))
    assert len(parsed.observations) == 15
    assert [o.note for o in parsed.observations if o.note] == ["OBS_FLAG=p"] * 3
    assert all(o.unit == "CP_MEUR" and o.area_label is None for o in parsed.observations)
    assert parsed.warnings
    population = parse(sample(POP))
    assert population.observations[0].dims["age"] == "TOTAL"
    assert population.observations[0].unit == "NR"
    assert population.datasets[0].description == "Synthetic population DSD"


@pytest.mark.parametrize("marker", [None, "", ":", "-", "…", "x", "秘匿"])
def test_missing_values_preserve_status_and_marker(marker):
    root = etree.fromstring(data_bytes(GDP))
    node = root.find(".//g:Obs", NS)
    value = node.find("g:ObsValue", NS)
    if marker is None:
        node.remove(value)
    else:
        value.set("value", marker)
    attrs = etree.SubElement(node, f'{{{NS["g"]}}}Attributes')
    for key, flag in (("OBS_STATUS", "e"), ("OBS_FLAG", "p")):
        etree.SubElement(attrs, f'{{{NS["g"]}}}Value', id=key, value=flag)
    obs = parse(sample(GDP, etree.tostring(root))).observations[0]
    assert obs.value is None
    assert "OBS_STATUS=e" in obs.note and "OBS_FLAG=p" in obs.note
    assert (f"OBS_VALUE={marker}" if marker else "OBS_VALUE=missing") in obs.note


def test_ids_ignore_order_value_and_snapshot_but_conflicts_fail():
    first = parse(sample())
    root = etree.fromstring(data_bytes(GOV))
    key = root.find(".//g:SeriesKey", NS)
    key[:] = list(reversed(key))
    root.find(".//g:ObsValue", NS).set("value", "123")
    revised = parse(sample(data=etree.tostring(root)))
    assert [o.obs_id for o in first.observations] == [o.obs_id for o in revised.observations]
    assert first.observations[0].snapshot_sha256 != revised.observations[0].snapshot_sha256
    assert len({o.obs_id for o in first.observations}) == 64
    series = root.find(".//g:Series", NS)
    series.append(deepcopy(series.find("g:Obs", NS)))
    assert len(parse(sample(data=etree.tostring(root))).observations) == 64
    series[-1].find("g:ObsValue", NS).set("value", "999")
    with pytest.raises(ParseError, match="冲突"):
        parse(sample(data=etree.tostring(root)))


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "not-a-number"])
def test_invalid_values_fail(raw):
    root = etree.fromstring(data_bytes(GDP))
    root.find(".//g:ObsValue", NS).set("value", raw)
    with pytest.raises(ParseError):
        parse(sample(GDP, etree.tostring(root)))


def test_mismatched_structure_or_missing_dimension_fails():
    item = sample(GDP)
    with pytest.raises(ParseError, match="DSD"):
        EurostatParser().parse(item.snapshot, item.data)
    root = etree.fromstring(item.data)
    root.find(".//g:SeriesKey", NS).remove(root.find(".//g:SeriesKey/g:Value", NS))
    with pytest.raises(ParseError, match="维度"):
        parse(sample(GDP, etree.tostring(root)))


@pytest.mark.parametrize(("label", "start", "end", "kind"), [
    ("2024-Q1", "2024-01-01", "2024-03-31", "quarter"),
    ("2024-S2", "2024-07-01", "2024-12-31", "half_year"),
    ("2024-02", "2024-02-01", "2024-02-29", "month"),
    ("2024-W01", "2024-01-01", "2024-01-07", "week"),
    ("2024-02-29", "2024-02-29", "2024-02-29", "day"),
])
def test_periods(label, start, end, kind):
    assert _period(label) == (date.fromisoformat(start), date.fromisoformat(end), TimeGranularity(kind))


@pytest.mark.parametrize("label", ["2024-13", "2024-02-30", "2024-W99", "2024-S3", "0000"])
def test_invalid_dates_fail(label):
    with pytest.raises(ParseError):
        _period(label)


def test_unknown_period_keeps_label_without_fetch_date_substitution():
    assert _period("unknown") == (None, None, TimeGranularity.other)
