import json
from decimal import Decimal
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.parsers.estat_api import EstatAPIParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

FIX = Path(__file__).parents[1] / 'fixtures/estat_api'
META = {'request_type': 'data', 'stats_data_id': '0003443838', 'unit': '万人'}


def single():
    obj = json.loads((FIX / 'getStatsData_0003443838_start1_limit10.json').read_bytes())
    info = obj['GET_STATS_DATA']['STATISTICAL_DATA']['RESULT_INF']
    info.pop('NEXT_KEY')
    info['TOTAL_NUMBER'] = info['TO_NUMBER']
    return obj


def sample(obj, meta=META):
    data = json.dumps(obj, ensure_ascii=False).encode()
    return ParseSample(make_snapshot('jp-estat-api', 'https://api.e-stat.go.jp/test', data), data, meta)


class TestParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return EstatAPIParser()

    @pytest.fixture
    def samples(self):
        return [sample(single())]


def test_table_and_errors():
    obj = single()
    rows = obj['GET_STATS_DATA']['STATISTICAL_DATA']['DATA_INF']['VALUE']
    rows[1]['$'] = '***'
    s = sample(obj)
    result = EstatAPIParser().parse(s.snapshot, s.data, s.meta)
    assert result.observations[0].value == Decimal(12547)
    assert result.observations[0].dims['cat01_name'] == '総人口'
    assert result.observations[1].value is None and '該当数値がないもの' in result.observations[1].note
    obj = json.loads((FIX / 'getStatsData_0003443838_start1_limit10.json').read_bytes())
    s = sample(obj)
    with pytest.raises(ParseError):
        EstatAPIParser().parse(s.snapshot, s.data, s.meta)
    for status in (1, 2):
        obj['GET_STATS_DATA']['RESULT']['STATUS'] = status
        s = sample(obj)
        if status == 1:
            assert EstatAPIParser().parse(s.snapshot, s.data, s.meta).warnings
        else:
            with pytest.raises(ParseError, match='STATUS 2'):
                EstatAPIParser().parse(s.snapshot, s.data, s.meta)


def test_cpi_skips_year_and_fiscal_year(tmp_path):
    metadata = json.loads((FIX / 'getMetaInfo_0003427113.json').read_bytes())['GET_META_INFO']['METADATA_INF']
    rows = [{'@tab': '1', '@cat01': '0001', '@area': '00000', '@time': code, '$': '100'}
            for code in ('2026000909', '2025000000', '2025100000')]
    body = {**metadata, 'RESULT_INF': {'TOTAL_NUMBER': 3, 'FROM_NUMBER': 1, 'TO_NUMBER': 3},
            'DATA_INF': {'VALUE': rows}}
    obj = {'GET_STATS_DATA': {'RESULT': {'STATUS': 0}, 'STATISTICAL_DATA': body}}
    path = tmp_path / 'cpi.json'
    path.write_text(json.dumps(obj, ensure_ascii=False))
    s = sample(json.loads(path.read_bytes()), {'stats_data_id': '0003427113', 'unit': '2020年=100'})
    result = EstatAPIParser().parse(s.snapshot, s.data, s.meta)
    assert len(result.observations) == 1 and result.observations[0].time_start.isoformat() == '2026-09-01'
    assert '2' in result.warnings[0]
