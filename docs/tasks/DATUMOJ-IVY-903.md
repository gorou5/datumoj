---
id: DATUMOJ-IVY-903
title: 检查点 M3 集成：工具层接真实实现、MCP、60 题全部门槛（IVY 完成线）
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-902", "DATUMOJ-IVY-502", "DATUMOJ-IVY-503", "DATUMOJ-IVY-603", "DATUMOJ-IVY-604", "DATUMOJ-IVY-704"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/wiring.py", "src/datumoj/cli.py", "tests/integration/test_full_eval.py", "results/DATUMOJ-IVY-903/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_full_eval.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-903/summary.md 完成说明"]
  review: {by: human, checklist: ["是否只改了接线", "全部门槛是否在真实实现上达成", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
分析工具和 MCP 服务器接上全部真实实现，`datumoj eval --gate` 在完整 fixture 语料上全部达标。这是第一阶段的完成线。

# 背景
开发线：集成。这是 IVY 版本检查点 M3 的集成门槛，也是 0.1 版本（IVY）的完成线。向量用 `HashEmbedder`（离线、确定性），真实 bge-m3 的效果在 H5 和 H6 由人验证。

本任务合并并通过人工验收后，打 tag `v0.1.0-ivy-m3`；H6 验收通过后再打 `v0.1.0-ivy`。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-903/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. `wiring` 把 DATUMOJ-IVY-603 工具层接上真实的 Retriever、CatalogStore、ObservationStore、Manifest、RawStore。
2. CLI 新增 `datumoj mcp`（启动 DATUMOJ-IVY-604 的服务器）和 `datumoj eval [--gate] [--report DIR]`（门槛不达标时退出码 1）。
3. 测试：在完整 fixture 语料上跑全部题目（≥ 60），断言所有门槛；通过 MCP 内存会话调用 `search_docs` 和 `cite`，验证结果能还原到原文。
4. 达不到就如实报告，不改各线模块。

# 不做
- 不改各开发线的模块。
