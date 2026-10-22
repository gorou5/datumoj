# DATUMOJ-TACHIBANA-301 完成说明

## 实现

- e-Stat 解析器沿用原有观测值转换，按快照和 `statInfId` 产出稳定的 `Dataset` 与 `Resource`。Dataset 保存清单表题、调查名、机关名（清单未提供时为空）、观测时间标签范围、地区层级、快照 URL / 哈希和表题单元格 locator。
- 为 locator 指向的表题单元格增加 `Document` 和同 locator 的 `Chunk`。现有 `AnalysisTools.cite` 会按 locator 检查 chunks，因此可由快照哈希和 Dataset locator 找回表题单元格。
- 新测试覆盖 XLSX、东京 CSV、大阪 CSV、重复解析稳定性、publisher 可选值、目录按表题和调查名检索、`cite` 回溯和 `query_sql` 返回数值。测试继承 `ParserSuite`，使用录制 fixture 和 `datumoj.testing` 假存储。

## 变更文件

- `src/datumoj/parsers/estat_tables.py`：生成目录记录、资源记录和可被 cite 定位的表题 chunk；未改观测值转换。
- `tests/parsers/test_estat_dataset.py`：新增解析器合格测试及目录、cite、SQL 行为测试。
- `results/DATUMOJ-TACHIBANA-301/tests.log`：记录验收命令和输出。
- `results/DATUMOJ-TACHIBANA-301/summary.md`：本完成说明。

## 验收

三条任务验收命令均通过，完整输出见 `tests.log`：

- `make check T=tests/parsers/test_estat_dataset.py`：9 passed。
- `make check T=tests/crawlers/test_estat_files.py`：67 passed。
- `make check T=tests/integration/test_stats_e2e.py`：24 passed。

沙箱不能初始化 uv 缓存，`make check` 按 Makefile 约定使用只读主仓库 `.venv` 执行；三个命令均正常完成。`git diff --check` 通过。未修改 fixtures、契约、目录库或流水线。

## 未验证与风险

未用真实持久化数据库单独跑 e-Stat 入库流水线；目录、cite 和 SQL 行为由现有 `AnalysisTools` 配合假存储验证，另有统计集成验收通过。表题 chunk 也会被全文检索索引，这是在解析器 scope 内支持 `cite` 的必要结果。未发现其他已知风险；无需人类确认。
