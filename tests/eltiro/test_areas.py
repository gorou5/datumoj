from datumoj.eltiro.areas import BY_SCHEME, ROWS, country, detect_scheme
from datumoj.eltiro.models import ISO_ALPHA3


def test_crosswalk_complete_bijective_and_leading_zeroes():
    assert len(ROWS) == 249
    assert {r[1] for r in ROWS} == ISO_ALPHA3
    for scheme, mapping in BY_SCHEME.items():
        assert len(mapping) == len(set(mapping.values())) == 249
        for code, alpha3 in mapping.items():
            assert country(code, scheme) == alpha3
    assert country("004", "ISO3166-1-numeric") == "AFG"
    assert country("4", "ISO3166-1-numeric") is None
    assert country("jp", "ISO3166-1-alpha2") is None
    assert country("JP", None) is None


def test_scheme_detection_never_guesses_unknown_mixed_or_conflicting_codes():
    assert detect_scheme(["JP", "US"], []) == "ISO3166-1-alpha2"
    assert detect_scheme(["JPN", "USA"], []) == "ISO3166-1-alpha3"
    assert detect_scheme(["392", "840"], []) == "ISO3166-1-numeric"
    for areas in ([], ["ZZZ"], ["JP", "USA"], ["13000"]):
        assert detect_scheme(areas, []) is None
    assert detect_scheme(["JP"], ["custom"]) == "custom"
    assert detect_scheme(["JP"], ["custom", "ISO3166-1-alpha2"]) is None


def test_eurostat_geo_is_declared_only_and_maps_el_uk_but_not_gr_gb_or_aggregates():
    assert [country(c, "Eurostat-geo") for c in ("EL", "UK", "DE")] == ["GRC", "GBR", "DEU"]
    assert [country(c, "Eurostat-geo") for c in ("GR", "GB", "EU27_2020", "EA20")] == [None] * 4
    alpha2 = BY_SCHEME["ISO3166-1-alpha2"]
    changed = {c for c in alpha2.keys() ^ BY_SCHEME["Eurostat-geo"].keys()}
    assert changed == {"GR", "GB", "EL", "UK"}
    assert all(BY_SCHEME["Eurostat-geo"][c] == a3 for c, a3 in alpha2.items() if c not in {"GR", "GB"})
    # Never auto-detected: alpha-2 stays alpha-2, a set with EL stays unknown, a declaration wins.
    assert detect_scheme(["DE", "FR"], []) == "ISO3166-1-alpha2"
    assert detect_scheme(["EL", "DE"], []) is None
    assert detect_scheme(["EL"], ["Eurostat-geo"]) == "Eurostat-geo"
