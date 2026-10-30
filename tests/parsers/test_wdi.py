import json
from decimal import Decimal
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.parsers.wdi import WDIParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

FIX = Path(__file__).parents[1] / 'fixtures/wdi'
META = {'request_type': 'data', 'indicators': {'NY.GDP.MKTP.CD': 'current US$',
        'NY.GDP.MKTP.CN': 'current LCU'}, 'countries': ['JPN', 'USA'], 'start_year': 2020, 'end_year': 2024}


def single_page():
    rows = []
    for path in sorted(FIX.glob('gdp*_p*.json')):
        rows.extend(json.loads(path.read_bytes())[1])
    return json.dumps([{'page': 1, 'pages': 1, 'total': len(rows)}, rows]).encode()


def sample(data, meta=META):
    return ParseSample(make_snapshot('intl-wdi', 'https://api.worldbank.org/v2/test', data), data, meta)


class TestParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return WDIParser()

    @pytest.fixture
    def samples(self):
        data = (FIX / 'indicator_NY.GDP.MKTP.CN.json').read_bytes()
        return [sample(single_page()), sample(data, {'request_type': 'metadata', 'indicator': 'NY.GDP.MKTP.CN'})]


def test_values_and_null():
    payload = json.loads(single_page())
    payload[1][0]['value'] = None
    data = json.dumps(payload).encode()
    s = sample(data)
    result = WDIParser().parse(s.snapshot, data, s.meta)
    assert len(result.observations) == 19
    assert any('NY.GDP.MKTP.CD: 1' in warning for warning in result.warnings)
    jpn = next(o for o in result.observations if o.area_code == 'JPN' and o.time_label == '2023' and o.table_key.endswith('.CD'))
    assert jpn.value == Decimal('4384854269961.93')
    assert all(o.unit == 'current LCU' for o in result.observations if o.table_key.endswith('.CN'))
    assert not any(o.area_code == 'JPN' and o.time_label == '2024' and o.table_key.endswith('.CD') for o in result.observations)


@pytest.mark.parametrize('path', sorted(FIX.glob('gdp*_p*.json')))
def test_reject_partial(path):
    data = path.read_bytes()
    s = sample(data)
    with pytest.raises(ParseError):
        WDIParser().parse(s.snapshot, data, s.meta)


def test_reject_single_page_with_missing_rows():
    payload = json.loads(single_page())
    payload[0]['total'] += 1
    data = json.dumps(payload).encode()
    s = sample(data)
    with pytest.raises(ParseError):
        WDIParser().parse(s.snapshot, data, s.meta)
