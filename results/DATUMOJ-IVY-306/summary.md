# DATUMOJ-IVY-306 完成说明

## 修改

- `DuckDBObservationStore._guard` 将 SQL 编码为 UTF-8 字节串；DuckDB tokenizer 返回的位置用于字节串切片和分号检查，避免多字节字符导致字符下标错位。
- 新增 Unicode 查询测试，覆盖日文条件查询、Unicode 字面量之后拦截 `read_csv`、字面量中含禁用函数名时允许查询，以及全角字符、emoji、混合别名和注释。
- 未修改原有 `tests/store/test_observations.py` 和任何契约。

## 验收

- `make check T=tests/store/test_observations_unicode.py`：通过，5 passed。完整输出见 [test-observations-unicode.log](test-observations-unicode.log)。
- `make check T=tests/store/test_observations.py`：通过，83 passed。完整输出见 [test-observations.log](test-observations.log)。
- 两次运行均因沙箱中 `uv` 不可用而由 Makefile 自动改用 `/Users/aaron/workspace/datumoj/.venv/bin/python`；未安装依赖。

## 评审清单

- 日文、全角字符和 emoji 所在 SQL 均有覆盖。
- 含多字节字面量后出现的 `read_csv` 仍被拒绝；字面量里的 `read_csv` 和 `東京read_csv` 不被误拒。
- 原 DATUMOJ-IVY-303 测试 83 项全部通过。
- 变更仅在任务 scope 内；文件列表见 [changed-files.txt](changed-files.txt)。

跳过：无。未核对：除任务要求的两条验收外，未运行其他检查。风险：tokenizer 字节偏移按 UTF-8 边界解码；本次 Unicode 测试与原有测试均通过。
