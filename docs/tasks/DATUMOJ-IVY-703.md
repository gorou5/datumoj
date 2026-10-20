---
id: DATUMOJ-IVY-703
title: 目录与统计评测题 20 道
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-701"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["eval/questions/catalog.yaml", "eval/questions/stats.yaml", "tests/eval/test_questions_stats.py", "results/DATUMOJ-IVY-703/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: /opt/homebrew/bin/uv run --frozen pytest -q tests/eval/test_questions_stats.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-703/summary.md 完成说明"]
  review: {by: human, checklist: ["期望数值是否来自原表", "是否有混用不同调查的题", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
目录检索和统计数值各 10 道题；数值题的期望值从原表人工核对而来。

# 背景
开发线：G 评测线。只用 `tests/fixtures/ckan_egov/`、`stat_dashboard/`、`estat_files/` 里有的内容出题。数值必须从原表读，不能用被测代码算出来再填回去。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-703/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 目录题 10 道：问“有没有关于 X 的数据”，期望命中具体 dataset；至少 3 道中文或英文（附日文改写）。
2. 数值题 10 道：`expect.value.sql` 查 `observations` 视图，`equals` 为原表数值；`notes` 写明原表文件和单元格位置。
3. 测试：题库能加载；每道数值题注明的原表文件存在，并且在测试里直接读原表单元格，确认等于 `equals`。

# 不做
- 不改 `src/`。
