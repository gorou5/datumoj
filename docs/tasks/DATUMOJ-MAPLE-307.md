---
id: DATUMOJ-MAPLE-307
title: 聚合的证据状态（D-05）：evidence_state 和输入的表、快照、过滤范围
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-306"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/store/observations.py", "src/datumoj/analysis/tools.py", "tests/analysis/test_evidence_state.py", "tests/regressions/test_audit_307.py", "results/DATUMOJ-MAPLE-307/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/analysis/test_evidence_state.py
    - cmd: make check T=tests/analysis
    - cmd: make check T=tests/regressions/test_audit_307.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-307/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
`query_sql` 的结果说明自己的证据有多完整：每行都有出处、部分有、还是没有（例如聚合查询），并给出用到了哪些表和快照，回答时能据此说明口径。

# 背景
开发线：C 统计线。审核 D-05：聚合查询没有 `obs_id` 时 `_provenance` 返回空，工具层只要有行就是 `gap_state=ok`，没有单独的证据状态。契约（MAPLE-002）已提供 `EvidenceState` 和 `ToolResult.evidence_state` / `evidence_inputs`。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-307/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `query_sql` 返回 `evidence_state`：每行都关联到出处为 `complete`；部分为 `partial`；没有任何行能关联为 `unavailable`。`gap_state` 仍只表示有没有结果。
2. `evidence_inputs`：SQL 实际读到的表（`source_id`、`table_key`）及这些表的快照 sha256 / snapshot_id、抓取时间范围；能从 SQL 的 WHERE 里简单识别出的过滤（时间、地区）原样列出，识别不了时写“未识别”，不猜。实现方式写进 summary（例如用 DuckDB 的 `EXPLAIN` 或在视图上加表来源列）。
3. `unavailable` / `partial` 时 `warnings` 给出怎么改 SQL 才能拿到逐行出处。
4. 新测试 `tests/analysis/test_evidence_state.py` 覆盖三种状态、`evidence_inputs` 列出的表和快照、过滤识别与未识别。审核回归测试 `tests/regressions/test_audit_307.py`：用合成数据离线复现审核场景——不带 `obs_id` 的聚合查询（如 `SELECT count(*)`）的 `evidence_state` 不是 `complete`——断言修好以后的行为，必须通过（不能标 skip / xfail）。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改 `gap_state` 的含义。
