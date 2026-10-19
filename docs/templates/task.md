---
id: PREFIX-001
title: 一句话标题
project: PROJECT
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/**", "tests/**", "results/PREFIX-001/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md"]
protected: []
acceptance:
  automated:
    - cmd: pytest -q tests/test_example.py
  evidence: ["测试输出日志", "变更文件清单"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否有硬编码数值"]}
  human: sampled
max_attempts: 2
---
# 目标
这个任务完成后，什么事情变成真的（一两句）。

# 背景
为什么要做；相关的文件和已有的代码在哪。

# 要求
1. 逐条写，每条都能被测试或审查核对。
2. 测试写在 `tests/test_example.py`，覆盖上面每一条。

# 不做
- 明确不在这个任务里做的事。
