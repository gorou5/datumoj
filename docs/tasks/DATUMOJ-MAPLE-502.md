---
id: DATUMOJ-MAPLE-502
title: 过滤召回（D-07）：日期、类型过滤前置或有界补取，预算用完返回截断而不是零命中
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-404"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/index/hybrid.py", "src/datumoj/index/fts.py", "src/datumoj/index/vector.py", "src/datumoj/analysis/tools.py", "tests/index/test_filtered_recall.py", "tests/regressions/test_audit_502.py", "results/DATUMOJ-MAPLE-502/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_filtered_recall.py
    - cmd: make check T=tests/index
    - cmd: make check T=tests/analysis
    - cmd: make check T=tests/regressions/test_audit_502.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-502/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
带日期或文档类型过滤检索时，只要库里有符合条件的资料就能找到，不会因为先截了前 k 个候选再过滤而误报 `zero_hits`；候选补取有上限，用完还没凑够时明说“结果被截断”。

# 背景
开发线：E 检索线。审核 D-07：`HybridRetriever.hybrid_search`（`index/hybrid.py`）的关键词和向量各只取 k 个候选，之后才按 Chunk 日期过滤；`search_docs(kinds=...)`（`analysis/tools.py`）把候选扩到 `MAX_K=50` 后再筛类型，也不完整。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-502/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 能前置的过滤前置：关键词索引（SQLite FTS5，`index/fts.py`）按日期和类型过滤在 SQL 里做（需要的列可以加到索引表，schema 变化要有迁移或重建说明）；向量检索在 LanceDB 查询里用 where 过滤（`index/vector.py`）。
2. 不能前置的，按批扩大候选补取（例如每次翻倍），总候选数有上限（模块常量）；上限用完仍不足 k 条时，结果带 `truncated`（写进 `gap_detail` 或 warnings，按现有结果结构选，写进 summary），且不判 `zero_hits`。
3. 无过滤时的结果和排序与现在一致。
4. 新测试 `tests/index/test_filtered_recall.py` 覆盖：符合条件的文档排在 k 之外时仍能找到、类型过滤同理、补取上限用完返回截断、无过滤结果不变。审核回归测试 `tests/regressions/test_audit_502.py`：用合成数据离线复现审核场景——符合日期过滤的文档在不过滤时排在 top-k 之外，带日期过滤检索仍能找到它、不报 `zero_hits`——断言修好以后的行为，必须通过（不能标 skip / xfail）。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改相关性门槛（ADR 0003）。
