---
id: DATUMOJ-IVY-707
title: 修复：评测框架按题型取证（目录题认目录检索、数值题取 SQL 出处、时间题认施行日）
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eval/framework.py", "eval/questions/stats.yaml", "tests/eval/test_framework_dispatch.py", "tests/eval/test_questions_stats.py", "results/DATUMOJ-IVY-707/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/eval/test_framework.py", "tests/eval/test_framework_numeric.py"]
acceptance:
  automated:
    - cmd: make check T=tests/eval/test_framework_dispatch.py
    - cmd: make check T=tests/eval/test_framework.py
    - cmd: make check T=tests/eval/test_framework_numeric.py
    - cmd: make check T=tests/eval/test_questions_stats.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-707/summary.md 完成说明"]
  review: {by: other_family, checklist: ["目录命中是否只认真实目录检索的结果（不能把期望答案塞进结果）", "数值出处是否仍要求能 cite 还原", "统计题 SQL 改动后是否仍只返回一行且值不变", "原有 701/706 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
评测框架按题目要证明的东西取证，不再把所有题都交给文档检索：数据集类命中可以由目录检索提供，数值题从 SQL 结果拿到逐行出处，时间题承认施行日口径。

# 背景
开发线：G 评测线（修复 DATUMOJ-IVY-701/706）。集成任务 DATUMOJ-IVY-903 在真实实现上跑完 60 题，发现评测框架和系统设计对不上：
- 目录和统计来源的解析器产出的是 Dataset 和 Observation，不生成文档块；它们本来就该通过 `search_catalog` 和 SQL 查。框架却对每道题都只调 `Retriever.search`，于是目录 10 题、统计 10 题的 Recall 全是 0，跨来源题里的统计命中也找不到。902 用目录接口评测时目录 Recall 是 1.00。
- 统计题 SQL 都是 `SELECT value ...`，结果不含 `obs_id`，`DuckDBObservationStore` 就不返回 provenance，706 加的“SQL 出处行”路径因此没有证据，数值出现率为 0。
- 时间语义只认 `period` 和 `published_at`，法令出处的口径是 `effective`（施行基准日），被误判。

计划见 `docs/PLAN.md`；测试全部离线，不能改依赖和契约。

# 要求
1. `evaluate(...)` 增加可选参数 `catalog: CatalogStore | None = None`。传入时，对每道题另外用题目的 `queries` 调 `catalog.search`（按 `source_ids` 过滤），得到数据集排名。期望命中按 `doc_key` 判定：在文档检索前 k 条里出现，**或**和目录检索前 k 条的 `dataset_key` 相同，都算命中；MRR 取两者中更靠前的名次。没有传 `catalog` 时行为不变。
2. 数值题：`_scalar` 优先取名为 `value` 的列；把 `eval/questions/stats.yaml` 里 10 道数值题的 SQL 都改成 `SELECT obs_id, value FROM ...`（其他条件一字不改），这样结果带 provenance，706 的出处行路径就能生效。改完后每题仍只返回一行、值不变（`tests/eval/test_questions_stats.py` 要跟着验证这一点，它不在 protected 里，可以改）。
3. 时间语义：引用的 `time_basis` 为 `effective` 且有 `effective_at` 时也算带了时间口径（和 `period`、`published_at` 并列）。
4. 原有 `tests/eval/test_framework.py`、`tests/eval/test_framework_numeric.py` 已列为 protected，必须全部通过。
5. 新测试 `tests/eval/test_framework_dispatch.py`（用 `datumoj.testing.fakes`）：目录题只靠目录检索命中；跨来源题一部分命中来自文档、一部分来自目录；不传 catalog 时与原行为一致；`SELECT obs_id, value` 的数值题出现率为 1；effective 口径的时间题通过。

# 不做
- 不改检索、目录库、观测值库、契约。
- 除第 2 条外不改题库。
