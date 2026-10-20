"""Shared Japanese text helpers used by DATUMOJ parsers."""

from __future__ import annotations

import codecs
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta
from typing import NamedTuple


class WarekiYear(NamedTuple):
    year: int
    is_fiscal: bool


class WarekiDate(NamedTuple):
    year: int
    month: int | None
    day: int | None
    is_fiscal: bool


_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
_NOTES = {"-", "…", "x", "X", "秘匿", "－", "***"}
_ERA_START = {
    "大正": date(1912, 7, 30),
    "昭和": date(1926, 12, 25),
    "平成": date(1989, 1, 8),
    "令和": date(2019, 5, 1),
}
_ERA_MAX_YEAR = {"大正": 15, "昭和": 64, "平成": 31}
_KANJI_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_SMALL_UNITS = {"十": 10, "百": 100, "千": 1_000}
_LARGE_UNITS = {"万": 10_000, "億": 100_000_000, "兆": 1_000_000_000_000}


def decode_bytes(data: bytes, declared: str | None = None) -> tuple[str, str, bool]:
    """Decode common Japanese government text encodings; mismatch means declaration fallback/conflict."""
    bom_encodings = (
        (codecs.BOM_UTF32_LE, "utf-32", {"utf-32", "utf-32-le"}),
        (codecs.BOM_UTF32_BE, "utf-32", {"utf-32", "utf-32-be"}),
        (codecs.BOM_UTF8, "utf-8-sig", {"utf-8", "utf-8-sig"}),
        (codecs.BOM_UTF16_LE, "utf-16", {"utf-16", "utf-16-le"}),
        (codecs.BOM_UTF16_BE, "utf-16", {"utf-16", "utf-16-be"}),
    )
    if data:
        for bom, encoding, compatible in bom_encodings:
            if data.startswith(bom):
                declared_name = _codec_name(declared)
                return data.decode(encoding), encoding, declared is not None and declared_name not in compatible

    mismatch = False
    if declared is not None:
        try:
            encoding = codecs.lookup(declared).name
            return data.decode(encoding), encoding, False
        except (LookupError, UnicodeDecodeError):
            mismatch = True

    try:
        return data.decode("utf-8"), "utf-8", mismatch
    except UnicodeDecodeError:
        pass

    try:
        from charset_normalizer import from_bytes

        match = from_bytes(data).best()
        if match is not None:
            encoding = _supported_encoding(match.encoding)
            if encoding is not None:
                return data.decode(encoding), encoding, mismatch
    except ImportError:
        pass

    for encoding in ("cp932", "shift_jis", "euc_jp"):
        try:
            return data.decode(encoding), encoding, mismatch
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("japanese", data, 0, len(data), "cannot decode as UTF-8, CP932, Shift_JIS, or EUC-JP")


def _codec_name(encoding: str | None) -> str | None:
    if encoding is None:
        return None
    try:
        return codecs.lookup(encoding).name
    except LookupError:
        return None


def _supported_encoding(encoding: str) -> str | None:
    name = _codec_name(encoding)
    return name if name in {"utf-8", "cp932", "shift_jis", "euc_jp"} else None


def normalize(text: str) -> str:
    """Apply NFKC, remove common zero-width format characters, and collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    return " ".join("".join(char for char in text if char not in _ZERO_WIDTH).split())


def parse_number(s: str) -> tuple[Decimal | None, str | None]:
    """Parse a non-negative decimal or a triangle-marked negative; preserve non-numeric notes."""
    stripped = s.strip()
    if stripped in _NOTES:
        return None, s

    value = normalize(stripped)
    match = re.fullmatch(r"([△▲])?\+?((?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?)", value)
    if match is None:
        return None, s
    try:
        number = Decimal(match.group(2).replace(",", ""))
    except InvalidOperation:
        return None, s
    return (-number if match.group(1) else number), None


def kansuji_to_int(text: str) -> int:
    """Convert Japanese digit/unit numerals, optionally wrapped as an article number."""
    token = normalize(text)
    if token.startswith("第"):
        token = token[1:]
    if token.endswith("条"):
        token = token[:-1]
    token = normalize(token)
    if token == "元":
        return 1
    if token.isdecimal():
        return int(token)
    if not token or any(
        char not in _KANJI_DIGITS and char not in _SMALL_UNITS and char not in _LARGE_UNITS for char in token
    ):
        raise ValueError(f"not a Japanese integer: {text!r}")

    total = section = 0
    digit_buffer: int | None = None
    digit_count = 0
    last_small_unit = 10_000
    last_large_unit = 10**20
    for char in token:
        if char in _KANJI_DIGITS:
            digit_buffer = (digit_buffer or 0) * 10 + _KANJI_DIGITS[char]
            digit_count += 1
        elif char in _SMALL_UNITS:
            unit = _SMALL_UNITS[char]
            if unit >= last_small_unit or digit_count > 1:
                raise ValueError(f"misordered Japanese numeral: {text!r}")
            section += (1 if digit_buffer is None else digit_buffer) * unit
            digit_buffer, digit_count, last_small_unit = None, 0, unit
        else:
            unit = _LARGE_UNITS[char]
            if unit >= last_large_unit:
                raise ValueError(f"misordered Japanese numeral: {text!r}")
            group = section + (0 if digit_buffer is None else digit_buffer)
            total += (1 if group == 0 else group) * unit
            section, digit_buffer, digit_count = 0, None, 0
            last_small_unit, last_large_unit = 10_000, unit
    return total + section + (0 if digit_buffer is None else digit_buffer)


def article_number(text: str) -> str:
    """Return article and sub-article numerals as an underscore-separated key."""
    token = normalize(text)
    match = re.fullmatch(r"第?([^条の]+)条(?:の(.+))?", token)
    if match is None:
        raise ValueError(f"not an article number: {text!r}")
    parts = [match.group(1), *(match.group(2).split("の") if match.group(2) else [])]
    return "_".join(str(kansuji_to_int(part)) for part in parts)


def wareki_to_date(text: str) -> WarekiDate | None:
    """Parse a Japanese era year/month/day without inventing missing month or day values."""
    token = normalize(text)
    numeral = r"(?:元|[0-9]+|[〇零一二三四五六七八九十百千万億兆]+)"
    pattern = rf"^(令和|平成|昭和|大正)({numeral})(?:年度|年(?:({numeral})月(?:({numeral})日)?)?)$"
    match = re.fullmatch(pattern, token)
    if match is None:
        if token.startswith(tuple(_ERA_START)):
            raise ValueError(f"invalid Japanese era date: {text!r}")
        return None

    era, era_year_text, month_text, day_text = match.groups()
    fiscal = token.endswith("年度")
    era_year = kansuji_to_int(era_year_text)
    if era_year < 1 or (era in _ERA_MAX_YEAR and era_year > _ERA_MAX_YEAR[era]):
        raise ValueError(f"invalid {era} year: {era_year_text!r}")
    year = _ERA_START[era].year + era_year - 1
    month = kansuji_to_int(month_text) if month_text else None
    day = kansuji_to_int(day_text) if day_text else None
    if month is not None and not 1 <= month <= 12:
        raise ValueError(f"invalid month: {month}")
    if day is not None and month is None:
        raise ValueError("a day requires a month")
    if day is not None and not 1 <= day <= 31:
        raise ValueError(f"invalid day: {day}")

    parsed = WarekiDate(year, month, day, fiscal)
    _validate_era_range(era, parsed)
    return parsed


def wareki_year(text: str) -> WarekiYear | None:
    parsed = wareki_to_date(text)
    return WarekiYear(parsed.year, parsed.is_fiscal) if parsed is not None else None


def _validate_era_range(era: str, parsed: WarekiDate) -> None:
    start = _ERA_START[era]
    next_starts = sorted(value for value in _ERA_START.values() if value > start)
    end = next_starts[0] - timedelta(days=1) if next_starts else date.max
    first = date(parsed.year, parsed.month or 1, parsed.day or 1)
    if parsed.day is not None:
        last = first
    elif parsed.month is not None:
        next_month = date(parsed.year + (parsed.month == 12), parsed.month % 12 + 1, 1)
        last = next_month - timedelta(days=1)
    else:
        last = date(parsed.year, 12, 31)
    if first > end or last < start:
        raise ValueError(f"date falls outside the {era} era")


__all__ = [
    "WarekiDate",
    "WarekiYear",
    "article_number",
    "decode_bytes",
    "kansuji_to_int",
    "normalize",
    "parse_number",
    "wareki_to_date",
    "wareki_year",
]
