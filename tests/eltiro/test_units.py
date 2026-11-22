"""The unit table lists only the pilot units; everything else is compared as written."""

import pytest

from datumoj.eltiro.units import UNIT_CODES, canonical_unit


@pytest.mark.parametrize(("source", "unit", "code"), [
    ("intl-imf-weo", "USD", "USD"),
    ("intl-imf-weo", "XDC", "XDC"),
    ("intl-wdi", "current US$", "USD"),
    ("intl-wdi", "current LCU（各国本币）", "XDC"),
    ("intl-oecd", "USD_EXC", "USD"),
    ("intl-imf-weo", "PT", "PERCENT"),
    ("eu-eurostat", "PC_GDP", "PERCENT"),
])
def test_listed_units_map_to_their_code(source, unit, code):
    assert canonical_unit(source, unit) == code


@pytest.mark.parametrize(("source", "unit"), [
    ("intl-wdi", "US dollars"),  # not a listed spelling
    ("intl-wdi", "USD"),  # "USD" is only listed for IMF; for WDI it is simply the original text "USD"
    ("intl-oecd", "JPY"),  # a currency code is never read as national currency
    ("eu-eurostat", "MIO_EUR"),
    ("unknown-source", "current US$"),
])
def test_unlisted_units_stay_as_written(source, unit):
    assert canonical_unit(source, unit) == unit
    assert canonical_unit(source, unit, scale_confirmed=True) == unit


def test_oecd_multiplier_suffix_is_dropped_only_when_the_scale_is_confirmed():
    assert canonical_unit("intl-oecd", "USD_EXC ×10^6") == "USD_EXC ×10^6"
    assert canonical_unit("intl-oecd", "USD_EXC ×10^6", scale_confirmed=True) == "USD"
    assert canonical_unit("intl-oecd", "JPY ×10^-3", scale_confirmed=True) == "JPY"
    # Other sources never carry such a suffix, so nothing is stripped there.
    assert canonical_unit("intl-wdi", "current US$ ×10^6", scale_confirmed=True) == "current US$ ×10^6"


def test_missing_unit_stays_missing():
    assert canonical_unit("intl-wdi", None, scale_confirmed=True) is None


def test_every_entry_is_keyed_by_source_and_original_text():
    assert all(isinstance(key, tuple) and len(key) == 2 and all(key) for key in UNIT_CODES)
