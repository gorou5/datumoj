---
id: DATUMOJ-MAPLE-404
title: 引用绑定快照和资源（D-03）：Citation 带 snapshot_id，cite 接受完整身份，只给 SHA 不唯一时返回歧义
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-002"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/analysis/tools.py", "src/datumoj/analysis/reports.py", "src/datumoj/index/hybrid.py", "src/datumoj/mcp_server.py", "src/datumoj/core/citations.py", "tests/analysis/test_citation_identity.py", "tests/test_mcp_server.py", "tests/test_evidence_regressions.py", "results/DATUMOJ-MAPLE-404/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/analysis/test_citation_identity.py
    - cmd: make check T=tests/analysis
    - cmd: make check T=tests/index
    - cmd: make check T=tests/test_mcp_server.py
    - cmd: make check T=tests/test_evidence_regressions.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-404/summary.md 完成说明"]
  review: {by: human, checklist: ["每条新引用是否都带 snapshot_id 且指向自己的资源", "只给 SHA 不唯一时是否返回歧义而不是猜", "MCP cite 参数和说明是否同步", "list_changes 是否不再全表扫描", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
每条引用都指向块或观测值自己所属的那次快照和资源；同样内容出现在两个 URL 时不会指错。`cite` 能按完整身份还原；只给 SHA 而它对应多个资源时，返回歧义并列出候选，不猜。

# 背景
开发线：D 目录与引用。审核 D-03：`AnalysisTools._snapshot`（`analysis/tools.py`）取“同 SHA 最近的成功快照”，`HybridRetriever`（`index/hybrid.py`）取 `snapshots[-1]`，`list_changes` 也按 SHA 找快照；`cite` 只有 SHA + locator（MCP 参数见 `mcp_server.py`）。Chunk 有 `doc_key` 和 `snapshot_sha256`，Document 来自哪个资源可由 manifest 里同 sha 的快照和 doc_key 的对应关系确定（ADR 0004）。契约（MAPLE-002）已给 `Citation.snapshot_id` 和歧义的表示。另外并入 CodeRabbit 在 #15 指出的问题：`list_changes` 找不到文档时对来源的全部 chunk 做全表扫描（每个事件一次）。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核问题的回归用例在 `tests/test_evidence_regressions.py`（MAPLE-002 写出，修好之前标 `xfail(strict=True)`，原因里写着负责的任务号）。修好本任务负责的用例后**删掉它们的 xfail 标记**，不改断言；其他任务的用例不动。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-404/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 块的引用：在同 sha、同 source 的成功快照里，选与该块文档资源一致的那个（文档 metadata 里的资源 URL，或 doc_key 与 URL 的对应规则，按 ADR 0004）；仍有多个时取顺序最新的、且 URL 一致；URL 无法确定时不再静默取最新，`warnings` 写明歧义。观测值同理（按 `source_id` + 表的快照）。
2. 所有新产生的 `Citation` 都填 `snapshot_id`（检索、`get_chunk`、`query_sql`、`list_changes`、报告）。如需共用的选择逻辑，放进新模块 `core/citations.py`。
3. `cite`：新增可选参数 `snapshot_id`、`resource_url`；给了就按它们精确还原；只给 SHA 时，若 SHA 对应多个资源（不同 URL），返回歧义结果（列出候选的 snapshot_id 和 URL），不还原。MCP 的 `cite` 工具同步加参数，说明文字写明用法。
4. `list_changes`：事件的文档查找不再对每个事件全表扫描 chunk（一次调用至多建一次索引，或用定向查询），结果与现在一致。
5. 新测试 `tests/analysis/test_citation_identity.py` 覆盖：同 SHA 两个 URL 时检索和 `get_chunk` 的引用各自正确、`cite` 精确还原、只给 SHA 时歧义、唯一时照常、`list_changes` 不随事件数全表扫描。`tests/test_mcp_server.py` 中 `cite` 相关断言允许为新参数调整。删掉 `tests/test_evidence_regressions.py` 里本任务负责的用例（xfail 原因里写着本任务号）的 xfail 标记，它们必须通过。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改 Chunk 和 Document 的存储结构（需要时写进 summary 问）。
