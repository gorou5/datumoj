from pathlib import Path

import pytest

from datumoj.rikolto.reference import code5_to_code6, code6_to_code5, lookup, normalize_area_label

ROOT = Path(__file__).resolve().parents[2]


def test_code_check_digit_round_trip():
    assert code5_to_code6("13000") == "130001"
    assert code6_to_code5("130001") == "13000"
    assert code5_to_code6("１３１０４") == "131041"
    with pytest.raises(ValueError):
        code6_to_code5("131017")
    with pytest.raises(ValueError):
        code5_to_code6("1234")


def test_prefecture_names_and_codes_are_fixed():
    prefectures = [lookup(f"{code:02}")[0] for code in range(1, 48)]
    assert len({area.code6[:2] for area in prefectures}) == 47
    assert lookup("東京都") == lookup("東京")
    assert lookup("東京都")[0].code5 == "13000"
    assert lookup("13") == lookup("13000") == lookup("130001")
    assert lookup("131016")[0].code5 == "13101"


def test_name_lookup_keeps_ambiguous_candidates_and_combined_labels():
    fuchu = lookup("府中市")
    assert {area.code6 for area in fuchu} == {"132063", "342084"}
    assert {area.code6 for area in lookup("伊達市")} == {"012335", "072133"}
    assert {area.code6 for area in lookup("泊村")} == {"014036", "016969"}
    assert lookup("東京都府中市") == (next(area for area in fuchu if area.code6 == "132063"),)
    assert lookup("東京都　新宿区")[0].code6 == "131041"
    assert lookup("大阪市北区")[0].code6 == "271276"
    assert normalize_area_label("　東京都　新宿区　") == "東京都新宿区"


def test_nationwide_and_invalid_codes():
    nationwide = lookup("00000")
    assert len(nationwide) == 1
    assert nationwide[0].code5 == "00000"
    assert lookup("000001") == nationwide
    assert lookup("全国") == nationwide
    assert lookup("131017") == ()
    assert lookup("99999") == ()
    assert lookup("1234") == ()
    assert lookup("not-an-area") == ()


def test_runtime_table_matches_read_only_fixture():
    runtime_file = ROOT / "src/datumoj/rikolto/reference/data/000925835_dantai_code_R060101.xlsx"
    fixture = ROOT / "tests/fixtures/area_codes/000925835_dantai_code_R060101.xlsx"
    assert runtime_file.read_bytes() == fixture.read_bytes()
