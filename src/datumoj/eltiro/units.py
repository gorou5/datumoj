"""Explicit unit comparison table: (source_id, unit text as published) -> unit code.

Only units used by the pilot demands (#002 GDP reconciliation, V07 Greek deficit) are listed, each with
its basis. A unit that is not listed is compared by its original text: nothing here infers a unit from
wording, and a currency code (e.g. OECD ``JPY``) is never mapped to ``XDC`` because the same text means a
different currency in another country. Magnitude is not part of the unit: it travels in ``Scale``.
"""

import re

PERCENT, USD, XDC = "PERCENT", "USD", "XDC"

UNIT_CODES = {
    # IMF SDMX/WEO UNIT column: SDMX codes, published as the codes themselves.
    ("intl-imf-weo", "USD"): USD,  # UNIT=USD on NGDPD (GDP, current prices, U.S. dollars)
    ("intl-imf-weo", "XDC"): XDC,  # UNIT=XDC on NGDP (GDP, current prices, national currency); the archive reader emits the same codes
    ("intl-imf-weo", "PT"): PERCENT,  # UNIT=PT on GGXCNL_NGDP (percent of GDP); the archive "Percent of GDP" is mapped to PT by the parser
    # World Bank WDI: the unit text is the one configured for the indicator in sources/intl-wdi.yaml.
    ("intl-wdi", "current US$"): USD,  # NY.GDP.MKTP.CD: GDP in current U.S. dollars
    ("intl-wdi", "current LCU（各国本币）"): XDC,  # NY.GDP.MKTP.CN: GDP in current local currency units
    # OECD SDMX: UNIT_MEASURE code when CURRENCY is empty/_Z; dataflow codelist: USD_EXC = US dollars, exchange rate converted.
    ("intl-oecd", "USD_EXC"): USD,
    # Eurostat unit dimension: PC_GDP is "percentage of GDP" (gov_10dd_edpt1 B9/GD).
    ("eu-eurostat", "PC_GDP"): PERCENT,
}

_MULTIPLE = re.compile(r"(.+?) ×10\^-?[0-9]+")  # the suffix intl-oecd's parser appends from UNIT_MULT
_WITH_MULTIPLE = {"intl-oecd"}


def canonical_unit(source_id: str, unit: str | None, *, scale_confirmed: bool = False) -> str | None:
    """Unit code for comparison; unlisted units come back unchanged (original text).

    When the value's scale is confirmed by source metadata, an OECD ``×10^n`` suffix is dropped before the
    lookup: the magnitude is then handled by ``scale_mismatch`` / ``rescale``. Without a confirmed scale
    the suffix stays part of the text, as before.
    """
    if unit is None:
        return None
    base = unit
    if scale_confirmed and source_id in _WITH_MULTIPLE and (match := _MULTIPLE.fullmatch(unit)):
        base = match[1]
    return UNIT_CODES.get((source_id, base), base)
