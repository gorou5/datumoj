"""契约的假实现（fakes）、合格测试（contract_suites）和造测试数据的工具（samples）。"""

try:
    import pytest
except ImportError:  # 只用假实现时不需要 pytest
    pass
else:
    # 让合格测试里的 assert 失败时也能显示详细的比较信息
    pytest.register_assert_rewrite("datumoj.rikolto.testing.contract_suites")
