import pytest

from datumoj.core.contracts import QueryRejected
from datumoj.store.observations import DuckDBObservationStore
from datumoj.testing.samples import make_observation


@pytest.fixture
def store(tmp_path):
    result = DuckDBObservationStore(tmp_path / "obs")
    result.write_table("jp-stat", "t1", [
        make_observation("o1", "13960000", area_label="東京都", locator="row=1"),
    ])
    return result


def test_japanese_literal_query(store):
    result = store.query_readonly(
        "SELECT area_label FROM observations WHERE area_label = '東京都'"
    )

    assert result.columns == ["area_label"]
    assert result.rows == [("東京都",)]


def test_forbidden_function_after_multibyte_literal_is_rejected(store):
    with pytest.raises(QueryRejected, match="read_csv"):
        store.query_readonly("SELECT '東京都' AS area_label FROM read_csv('/etc/passwd')")


@pytest.mark.parametrize("literal", ["read_csv", "東京read_csv"])
def test_forbidden_name_inside_unicode_literal_is_allowed(store, literal):
    result = store.query_readonly(f"SELECT '{literal}' AS value")

    assert result.rows == [(literal,)]


def test_fullwidth_emoji_aliases_and_unicode_comment(store):
    result = store.query_readonly(
        "SELECT '東京😀' AS \"地域名_A1\", 1 AS \"全角ＡＢＣ\", 2 AS \"mix_日本語\" "
        "/* 全角コメント１２３と絵文字🙂 */ -- 日本語コメント\n"
    )

    assert result.columns == ["地域名_A1", "全角ＡＢＣ", "mix_日本語"]
    assert result.rows == [("東京😀", 1, 2)]
