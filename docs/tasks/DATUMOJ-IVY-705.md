---
id: DATUMOJ-IVY-705
title: 修复：统计题 tokyo-ict 的 SQL 补上经营组织维度
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: low
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["eval/questions/stats.yaml", "tests/eval/test_questions_stats.py", "results/DATUMOJ-IVY-705/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "src/**"]
protected: ["tests/fixtures/**"]
acceptance:
  automated:
    - cmd: make check T=tests/eval/test_questions_stats.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-705/summary.md 完成说明"]
  review: {by: other_family, checklist: ["SQL 是否在真实解析结果上只返回一行且等于 849374", "其他题目是否完全没有改动", "测试是否用真实解析器而不是手写观测值", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
`eval/questions/stats.yaml` 里 `stat-estat-tokyo-ict-employees-2016` 的 SQL 在真实解析结果上只返回一行，等于原表 I977 的 849374。

# 背景
开发线：G 评测线（修复 DATUMOJ-IVY-703）。集成任务 DATUMOJ-IVY-902 发现：e-Stat 解析器（305）保留了“经营组织”维度（`dims` 里的 `legal_org_code`），现有 SQL 只按指标、地区、产业过滤，会返回 6 行（keiso4A.0000 总数 I977=849374，以及 0001–0005 五个分项）。902 已独立验证：再加上 `json_extract_string(dims, '$.legal_org_code') = 'keiso4A.0000'` 就只剩唯一一行 849374。

计划见 `docs/PLAN.md`；测试全部离线，不能改依赖和 `src/`。

# 要求
1. 只改这一道题的 `expect.value.sql`：补上经营组织 = 总数（`keiso4A.0000`）的条件；`equals` 仍是 849374。`notes` 里补一句为什么要这个条件。
2. 其余 19 道题一字不改。
3. `tests/eval/test_questions_stats.py`：确认已有的“用真实 `EstatTableParser` + `DuckDBObservationStore` 执行全部数值题 SQL、断言只返回一行且等于 equals”的测试覆盖这道题；如果原测试的解析方式和 902 的真实流水线不一致（例如没有保留经营组织维度，所以原来只返回一行），改成和真实流水线一致，确保这道题旧的 SQL 会失败、新的 SQL 会通过。

# 不做
- 不改解析器、评测框架、其他题库文件。
