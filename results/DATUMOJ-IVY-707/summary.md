# DATUMOJ-IVY-707 完成说明

## 修改

- `src/datumoj/eval/framework.py` 增加可选 `catalog` 参数。传入时按每题的 queries 调用 `CatalogStore.search`，用 `source_ids` 过滤；目录返回的 `dataset_key` 与文档命中共同计算 Recall，MRR 取较前的排名。省略 catalog 时沿用文档检索行为。
- 数值提取根据 SQL 列名优先读取 `value`。`eval/questions/stats.yaml` 的 10 条统计 SQL 仅将投影改为 `SELECT obs_id, value`，保留原有筛选条件。
- 时间评测接受具有 `effective` 口径及 `effective_at` 的引用。
- 新增 `tests/eval/test_framework_dispatch.py` 覆盖目录独立命中、跨来源混合命中、catalog 可选行为、SQL 出处行数值及施行日口径。带 `locator_prefix` 的预期项仍要求文档检索命中，因为目录结果不能证明行/单元格位置。更新统计题测试，核对每题仍为单行、数值不变，并包含一条可还原的 provenance。

## 验收

详见同目录的 `acceptance.log`。四条任务验收命令全部通过：调度测试 6 项、原框架测试 6 项、原数值测试 4 项、统计题库测试 5 项。

## 评审清单

- 目录命中来自实际 `catalog.search` 返回的 `dataset_key`；测试将目标数据集写入 FakeCatalogStore，再通过查询检索，不向结果注入预期答案。
- 数值出处仍经原 `_has_sql_number` 路径校验：只有观测值匹配、manifest 找到对应成功快照且 `cite` 能还原出处时才计入；原数值测试通过。
- 10 条 SQL 的筛选条件未改。统计题测试确认每条结果只有一行、`value` 等于预期且返回一条 provenance；该测试通过。
- 独立复核指出目录不能证明带 `locator_prefix` 的预期位置；已将此类预期排除在目录命中之外，并加回归测试。
- 原 `tests/eval/test_framework.py` 与 `tests/eval/test_framework_numeric.py` 分别 6 项、4 项通过。
- 变更仅涉及任务 scope 内的 framework、统计题库、指定测试和结果目录；未改 protected 文件、契约、夹具或依赖配置。

独立 `other_family` 复核完成；边界发现已修复并重新运行四条验收命令。

## 跳过项与风险

未运行全仓测试，只运行任务列出的四条验收命令；`uv` 不可用，命令按项目配置改用现有只读虚拟环境。
