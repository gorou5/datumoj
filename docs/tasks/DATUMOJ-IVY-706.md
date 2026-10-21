---
id: DATUMOJ-IVY-706
title: 修复：数值出现率同时认 SQL 结果的出处行
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eval/framework.py", "tests/eval/test_framework_numeric.py", "results/DATUMOJ-IVY-706/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/eval/test_framework.py"]
acceptance:
  automated:
    - cmd: make check T=tests/eval/test_framework_numeric.py
    - cmd: make check T=tests/eval/test_framework.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-706/summary.md 完成说明"]
  review: {by: other_family, checklist: ["SQL 出处行是否必须能还原到快照才算数（不能只看数值相等）", "文档检索命中里有期望值的旧判定是否保留", "数值不等或出处还原不了时是否仍判失败", "原有 701 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
评测框架的“数值出现率”（numeric_occurrence）在文档检索前 3 条之外，也承认 **SQL 查询结果的出处行**：只要某条出处行的值等于期望值，且这条出处能还原到快照里的原始数据，就算出现。

# 背景
开发线：G 评测线（修复 DATUMOJ-IVY-701）。PLAN 第 10 节的定义是“数值题的期望值必须原样出现在**引用的原文片段或数据行**里”。数值本来就走 `query_sql`，SQL 结果自带逐行出处（`QueryResult.provenance`，每行是带 `snapshot_sha256` 和 `locator` 的 Observation），这正是“数据行”。但 `src/datumoj/eval/framework.py` 约第 254–270 行只在文档检索的前 3 条命中里找。集成任务 DATUMOJ-IVY-902 中，e-Stat 统计单元格在关键词检索里排不进前列，导致 SQL 已经算对、出处也完整，这一项仍判失败。

计划见 `docs/PLAN.md`；测试全部离线，不能改依赖和契约。

# 要求
1. 保留现有判定：文档检索前 3 条的可还原引用里有期望值，就算出现。
2. 新增判定：`query_result.provenance` 里有一行 `value` 规范化后等于期望值，并且这一行的出处能用框架已有的 `cite` 还原（通过 `citation_for_observation` 生成 Citation 后调用 `cite`，结果有证据），就算出现。只看数值相等、不还原出处的，不算。
3. 两种都不满足时仍判失败，失败原因写清楚是哪种都没找到。
4. 原有 `tests/eval/test_framework.py` 已列为 protected，必须全部通过。
5. 新测试 `tests/eval/test_framework_numeric.py`（用 `datumoj.testing.fakes`）：文档命中里有期望值 → 通过；文档命中里没有、SQL 出处行有且能还原 → 通过；SQL 出处行数值相等但 `cite` 还原不了 → 失败；数值不等 → 失败。

# 不做
- 不改检索排序、不改题库、不改契约。
