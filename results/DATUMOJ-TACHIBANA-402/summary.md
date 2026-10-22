# DATUMOJ-TACHIBANA-402 完成说明

## 改动

- `src/datumoj/parsers/ckan_meta.py`：抽出单条 package 解析逻辑；目录分页解析时将快照 SHA 与 `entry=N` 写入每条 Dataset。解析器版本升至 `2`。
- `src/datumoj/wiring.py`：分页解析保留结果数组下标，并把非对象项作为解析警告跳过；`ckan_pages` 版本升至 `2`，使已有分页快照可重新解析。
- `src/datumoj/analysis/tools.py`：`search_catalog` 为有引用的 Dataset 生成 Citation，时间按 `modified_at` 的日期或 `no_data_date`，带来源许可字段；缺少引用时返回 null 并添加指定警告。`cite` 对 CKAN `package_search` 的 `entry=N` 从原始 JSON 中取出原记录并包装为 `SourceText`，保留记录的原始 JSON 表示；错误格式与越界有明确原因。其他来源的 `entry` 定位仍走原解析器。
- `tests/parsers/test_ckan_meta_snapshot.py`：用真实 CKAN 分页 fixture 运行 ParserSuite，并检查快照引用和版本。
- `tests/analysis/test_catalog_citation.py`：覆盖 CKAN Citation、记录还原、越界、错误快照格式、旧记录警告和 e-Stat `cell=A1` locator。
- `results/DATUMOJ-TACHIBANA-402/acceptance.log`：保存最终验收命令输出。

采用现有 `Dataset`、`Citation`、`Manifest` 契约及 `datumoj.testing` 的假实现/合格测试，没有改契约、存储结构或检索排序，也没有新增依赖。测试使用本地 fixture 和内存假实现，无网络访问。

## 验收

以下五条任务验收命令均通过，逐条完整输出见 `acceptance.log`：

- `make check T=tests/parsers/test_ckan_meta_snapshot.py` — 5 passed
- `make check T=tests/analysis/test_catalog_citation.py` — 2 passed
- `make check T=tests/analysis` — 63 passed
- `make check T=tests/store/test_catalog.py` — 19 passed
- `make check T=tests/integration` — 36 passed
- `git diff --check` — 通过

沙箱中的 `uv` 不可用，所有 `make check` 按 Makefile 预设回退到主仓库只读 `.venv`；测试均通过。集成测试输出 6 条既有 DuckDB `table_names()` 弃用警告。

## 核对与限制

- 变更仅在任务 scope 内；没有改受保护文件、契约或依赖。
- `ParserSuite` 已通过；新增测试全部使用真实 CKAN 分页 fixture 或本地构造数据，没有联网测试。
- 没有在实际持久化数据目录运行 `datumoj parse`；重解析由版本号变化触发，既有解析流水线的行为通过集成验收覆盖。
- `search_catalog` 输出新增可空 `citation` 字段；工具名称和参数不变。无需人类确认。

跳过了 scope 外文件；未核对真实持久化数据目录中的重解析结果。其余风险限于新增输出字段的消费者兼容性，现有验收未发现问题。
