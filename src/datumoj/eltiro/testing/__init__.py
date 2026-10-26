"""Offline fakes and reusable protocol qualification suites."""

try:
    import pytest
except ImportError:
    pass
else:
    pytest.register_assert_rewrite("datumoj.eltiro.testing.contract_suites")
