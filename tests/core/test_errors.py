"""Boundary checks for safe message placeholders and exception arguments."""

import pytest

from datumoj.rikolto.core.contracts import QueryRejected, QueryTimeout, TooManyRows
from datumoj.rikolto.core.errors import MAX_REASON_LENGTH, safe_reason


@pytest.mark.parametrize("name", ["a", "_", "A_09", "a" * 64])
def test_parameter_names_accept_only_bounded_ascii_identifiers(name):
    message = f"{name} 必须带时区"
    assert safe_reason(ValueError(message)) == f"ValueError: {message}"


@pytest.mark.parametrize("name", ["", "0abc", "a" * 65, "é", "a b", "a/b", "a\\b", "a'b", "a\nb", "a\x00b"])
def test_unsafe_parameter_names_are_hidden(name):
    assert safe_reason(ValueError(f"{name} 必须带时区")) == "ValueError"
    assert safe_reason(QueryRejected(f"不允许使用 '{name}'（读文件或执行 SQL 串）")) == "QueryRejected"


@pytest.mark.parametrize("number", ["0", "10", "0.001", "10.5"])
def test_timeout_numbers_allow_ascii_integers_and_decimals(number):
    message = f"查询超过 {number} 秒"
    assert safe_reason(QueryTimeout(message)) == f"QueryTimeout: {message}"


@pytest.mark.parametrize("number", ["-1", "+1", "1e3", "NaN", "inf", "１", ".5", "1.", "1\n", "1/2"])
def test_other_number_forms_are_hidden(number):
    assert safe_reason(QueryTimeout(f"查询超过 {number} 秒")) == "QueryTimeout"
    assert safe_reason(TooManyRows(f"结果超过 {number} 行，请加条件或聚合")) == "TooManyRows"


def test_integer_placeholders_do_not_allow_decimals():
    assert safe_reason(ValueError("k 必须是 1.0–100 的整数")) == "ValueError"
    assert safe_reason(TooManyRows("结果超过 1.5 行，请加条件或聚合")) == "TooManyRows"


def test_only_one_exact_string_argument_is_considered():
    class UntrustedText(str):
        def __str__(self):
            raise AssertionError("must not render untrusted strings")

    assert safe_reason(ValueError(UntrustedText("since 必须带时区"))) == "ValueError"
    assert safe_reason(ValueError("since 必须带时区", "private/file")) == "ValueError"
    assert safe_reason(ValueError()) == "ValueError"


def test_allowed_message_keeps_the_length_limit():
    result = safe_reason(QueryTimeout(f"查询超过 {'1' * MAX_REASON_LENGTH} 秒"))
    assert len(result) == MAX_REASON_LENGTH
    assert result.startswith("QueryTimeout: 查询超过 ")
    assert result.isprintable()
