from decimal import Decimal

import pytest

from datumoj.rikolto.text import (
    WarekiDate,
    WarekiYear,
    article_number,
    decode_bytes,
    kansuji_to_int,
    normalize,
    parse_number,
    wareki_to_date,
    wareki_year,
)


@pytest.mark.parametrize("encoding", ["utf-8", "cp932", "shift_jis", "euc_jp"])
def test_decode_bytes_detects_japanese_encodings(encoding):
    source = "日本の統計資料、令和五年度の人口"
    text, _, mismatch = decode_bytes(source.encode(encoding))
    assert text == source
    assert mismatch is False


@pytest.mark.parametrize("encoding", ["utf-8", "cp932", "shift_jis", "euc_jp"])
def test_decode_bytes_accepts_declared_encoding(encoding):
    source = "統計資料の日本語"
    text, _, mismatch = decode_bytes(source.encode(encoding), encoding)
    assert text == source
    assert mismatch is False


@pytest.mark.parametrize(
    ("source_encoding", "declared"),
    [("cp932", "utf-8"), ("euc_jp", "utf-8"), ("utf-8", "not-a-codec")],
)
def test_decode_bytes_falls_back_after_bad_declaration(source_encoding, declared):
    source = "日本の公的統計データ"
    text, _, mismatch = decode_bytes(source.encode(source_encoding), declared)
    assert text == source
    assert mismatch is True


@pytest.mark.parametrize(
    ("encoding", "bom"),
    [("utf-8", b"\xef\xbb\xbf"), ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"),
     ("utf-32-le", b"\xff\xfe\x00\x00"), ("utf-32-be", b"\x00\x00\xfe\xff")],
)
def test_decode_bytes_honors_bom(encoding, bom):
    source = "令和5年"
    text, _, mismatch = decode_bytes(bom + source.encode(encoding))
    assert text == source
    assert mismatch is False


def test_decode_bytes_marks_bom_declaration_conflict():
    text, encoding, mismatch = decode_bytes("\ufeff日本".encode(), "cp932")
    assert (text, encoding, mismatch) == ("日本", "utf-8-sig", True)


def test_decode_bytes_rejects_undecodable_bytes():
    with pytest.raises(UnicodeDecodeError):
        decode_bytes(b"\x81")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("  ＡＢＣ　１２３  ", "ABC 123"),
        ("a\tb\nc", "a b c"),
        ("　日本語　", "日本語"),
        ("令和５年度", "令和5年度"),
        ("A\u200bB", "AB"),
        ("A\u200cB", "AB"),
        ("A\u200dB", "AB"),
        ("A\u2060B", "AB"),
        ("A\ufeffB", "AB"),
        ("\r\n \t ", ""),
        ("①㈱", "1(株)"),
        ("ｶﾀｶﾅ", "カタカナ"),
    ],
)
def test_normalize(source, expected):
    assert normalize(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("０", Decimal("0")),
        ("１２３", Decimal("123")),
        ("1,234", Decimal("1234")),
        ("１２,３４５", Decimal("12345")),
        ("1,234.50", Decimal("1234.50")),
        ("＋１２３", Decimal("123")),
        ("△１２３", Decimal("-123")),
        ("▲1,234.5", Decimal("-1234.5")),
        ("△0", Decimal("-0")),
        ("  42  ", Decimal("42")),
        ("1.25", Decimal("1.25")),
        ("９,９９９,９９９", Decimal("9999999")),
    ],
)
def test_parse_number(source, expected):
    assert parse_number(source) == (expected, None)


@pytest.mark.parametrize("note", ["-", "…", "x", "X", "秘匿", "－", "***", "  x  "])
def test_parse_number_preserves_notes(note):
    assert parse_number(note) == (None, note)


@pytest.mark.parametrize("invalid", ["", "abc", "12abc", "1,23", "1,234,56", "1.2.3", "△", "NaN", "∞"])
def test_parse_number_preserves_unrecognized_input(invalid):
    assert parse_number(invalid) == (None, invalid)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("〇", 0),
        ("零", 0),
        ("一", 1),
        ("二〇二三", 2023),
        ("二十七", 27),
        ("第二十七条", 27),
        ("百二十三", 123),
        ("一百二十三", 123),
        ("千二百三十四", 1234),
        ("二千二十四", 2024),
        ("一万", 10000),
        ("一万二千三百", 12300),
        ("一億二万", 100020000),
        ("元", 1),
        ("１２３", 123),
    ],
)
def test_kansuji_to_int(source, expected):
    assert kansuji_to_int(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("第二十七条の二", "27_2"),
        ("第百二十三条", "123"),
        ("第二条の二の三", "2_2_3"),
        ("第２７条の２", "27_2"),
        ("27条の2", "27_2"),
    ],
)
def test_article_number(source, expected):
    assert article_number(source) == expected


@pytest.mark.parametrize("invalid", ["", "abc", "第二十十七条", "一万万", "一百百", "二の条"])
def test_kansuji_to_int_rejects_invalid_input(invalid):
    with pytest.raises(ValueError):
        kansuji_to_int(invalid)


@pytest.mark.parametrize("invalid", ["", "abc", "第条", "第二条の", "第二条の二の"])
def test_article_number_rejects_invalid_input(invalid):
    with pytest.raises(ValueError):
        article_number(invalid)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("令和元年", WarekiDate(2019, None, None, False)),
        ("令和5年", WarekiDate(2023, None, None, False)),
        ("令和５年", WarekiDate(2023, None, None, False)),
        ("令和五年", WarekiDate(2023, None, None, False)),
        ("令和二十年", WarekiDate(2038, None, None, False)),
        ("令和5年4月", WarekiDate(2023, 4, None, False)),
        ("令和５年４月", WarekiDate(2023, 4, None, False)),
        ("令和五年四月", WarekiDate(2023, 4, None, False)),
        ("令和元年5月1日", WarekiDate(2019, 5, 1, False)),
        ("令和5年4月1日", WarekiDate(2023, 4, 1, False)),
        ("平成元年", WarekiDate(1989, None, None, False)),
        ("平成三十一年", WarekiDate(2019, None, None, False)),
        ("平成31年4月30日", WarekiDate(2019, 4, 30, False)),
        ("昭和元年", WarekiDate(1926, None, None, False)),
        ("昭和64年1月7日", WarekiDate(1989, 1, 7, False)),
        ("大正元年", WarekiDate(1912, None, None, False)),
        ("大正15年12月24日", WarekiDate(1926, 12, 24, False)),
        ("令和5年度", WarekiDate(2023, None, None, True)),
        ("令和五年度", WarekiDate(2023, None, None, True)),
        ("令和元年度", WarekiDate(2019, None, None, True)),
    ],
)
def test_wareki_to_date(source, expected):
    assert wareki_to_date(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("令和5年", WarekiYear(2023, False)),
        ("平成元年", WarekiYear(1989, False)),
        ("令和5年度", WarekiYear(2023, True)),
        ("昭和64年", WarekiYear(1989, False)),
    ],
)
def test_wareki_year_marks_fiscal_year(source, expected):
    assert wareki_year(source) == expected


@pytest.mark.parametrize(
    "invalid",
    [
        "令和0年",
        "令和13月",
        "令和5年13月",
        "令和5年4月31日",
        "令和元年1月1日",
        "平成元年1月1日",
        "昭和64年2月",
        "平成32年",
        "大正16年",
        "明治元年",
    ],
)
def test_wareki_to_date_rejects_invalid_or_unsupported_dates(invalid):
    if invalid == "明治元年":
        assert wareki_to_date(invalid) is None
    else:
        with pytest.raises(ValueError):
            wareki_to_date(invalid)


def test_wareki_to_date_returns_none_for_non_wareki_text():
    assert wareki_to_date("2023年4月") is None
    assert wareki_year("not a date") is None
