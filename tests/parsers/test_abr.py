import csv
import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.core.models import parse_locator
from datumoj.rikolto.parsers.abr import MEMBER, AbrParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).parents[2]
DATA = (ROOT / 'tests/fixtures/abr/mt_town_city134023.csv.zip').read_bytes()
URL = 'https://data.address-br.digital.go.jp/mt_town/city/mt_town_city134023.csv.zip'


def sample(data=DATA):
    return ParseSample(make_snapshot('jp-abr', URL, data, content_type='text/html; charset=ascii'), data,
                       {'municipality_code': '134023'})


def parse(data=DATA):
    s = sample(data)
    return AbrParser().parse(s.snapshot, s.data, s.meta)


class TestAbrParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return AbrParser()

    @pytest.fixture
    def samples(self):
        return [sample()]

    @pytest.fixture
    def bad_samples(self):
        return [b'not a ZIP', DATA[:100]]


def test_all_four_towns_all_string_columns_empty_values_and_japanese_are_lossless():
    result = parse()
    document, = result.documents
    assert '東京都青ヶ島村' in document.title
    assert document.published_at is None and document.effective_at is None
    with ZipFile(io.BytesIO(DATA)) as z:
        rows = list(csv.DictReader(io.StringIO(z.read(MEMBER).decode('utf-8'))))
    assert len(rows) == len(result.chunks) == 4
    for chunk, row in zip(result.chunks, rows, strict=True):
        actual = json.loads(chunk.text.split('\n', 1)[1])
        assert actual == row and len(actual) == 38
        assert all(isinstance(value, str) for value in actual.values())
        assert actual['lg_code'] == '134023'
        assert actual['machiaza_id'].startswith('000')
        assert actual['efct_date'] == '1947-04-17' and actual['ablt_date'] == ''
        assert actual['ward'] == '' and actual['post_code'] == ''
        assert actual['city'] == '青ヶ島村' and actual['city_kana'] == 'アオガシマムラ'
        locator = parse_locator(chunk.locator)
        assert locator == {'member': MEMBER, 'row': str(rows.index(row) + 2)}
    first = json.loads(result.chunks[0].text.split('\n', 1)[1])
    assert first['machiaza_id'] == '0003000'
    assert first['oaza_cho'] == '休戸郷' and first['oaza_cho_kana'] == 'ヤスンドゴウ'


def archive(tmp_path, text, member=MEMBER, encoding='utf-8'):
    path = tmp_path / 'abr.synthetic.zip'
    with ZipFile(path, 'w', ZIP_DEFLATED) as z:
        z.writestr(member, text.encode(encoding))
    return path.read_bytes()


def source_text():
    with ZipFile(io.BytesIO(DATA)) as z:
        return z.read(MEMBER).decode('utf-8')


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp932'])
def test_encoding_is_detected_from_member_content(tmp_path, encoding):
    result = parse(archive(tmp_path, source_text(), encoding=encoding))
    assert [c.text for c in result.chunks] == [c.text for c in parse().chunks]


@pytest.mark.parametrize('change', ['municipality', 'town', 'duplicate', 'short', 'long', 'header', 'empty', 'member'])
def test_invalid_rows_or_wrong_archive_fail_atomically(tmp_path, change):
    text = source_text()
    member = MEMBER
    if change == 'municipality':
        text = text.replace('134023,0003000', '134024,0003000')
    elif change == 'town':
        text = text.replace('0003000', '3000')
    elif change == 'duplicate':
        text = text.replace('0004000', '0003000')
    elif change == 'short':
        text = text.replace('134023,0003000,', '0003000,', 1)
    elif change == 'long':
        text = text.replace('134023,0003000,', '134023,0003000,extra,', 1)
    elif change == 'header':
        text = text.replace('lg_code', 'unknown', 1)
    elif change == 'empty':
        text = text.splitlines()[0]
    else:
        member = 'other.csv'
    with pytest.raises(ParseError):
        parse(archive(tmp_path, text, member))


def test_wrong_municipality_metadata_is_rejected():
    s = sample()
    with pytest.raises(ParseError, match='requires municipality'):
        AbrParser().parse(s.snapshot, s.data, {'municipality_code': '134024'})
