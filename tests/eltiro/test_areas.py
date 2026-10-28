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
