---
id: DATUMOJ-TACHIBANA-603
title: 工具层与 MCP：list_changes，search_docs 按文档类型过滤
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-601", "DATUMOJ-TACHIBANA-402"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/analysis/tools.py", "src/datumoj/mcp_server.py", "tests/analysis/test_list_changes.py", "tests/test_mcp_server.py", "results/DATUMOJ-TACHIBANA-603/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_mcp_server_threads.py", "tests/analysis/test_tools.py"]
acceptance:
  automated:
    - cmd: make check T=tests/analysis/test_list_changes.py
    - cmd: make check T=tests/test_mcp_server.py
    - cmd: make check T=tests/test_mcp_server_threads.py
    - cmd: make check T=tests/analysis
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-603/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
在 Claude 里能问“这周资料库里有什么变化”，拿到带出处的变化列表；检索时能只看某类文档（例如只看 PDF 或政策网页）。

# 背景
开发线：F 入口线。计划 6.4 节。工具层在 `analysis/tools.py`（`AnalysisTools`），MCP 在 `mcp_server.py`（单工作线程，工具清单有测试）。变化日志由 601 接进 `wiring`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-603/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `list_changes(since=None, until=None, sources=None, kinds=None, limit=100)`：`since` 默认 7 天前；返回 `ToolResult`，`data` 为事件列表，每条含 `kind`、`source_id`、`doc_key`、标题（如能取到）、`summary`、`detected_at`，以及 `citation`（有新快照的用新快照，删除类用旧快照）；没有事件时 `gap_state = zero_hits`；参数校验沿用 `_check_int` 等现有写法。
2. `search_docs` 加可选参数 `kinds`（`DocKind` 的值列表）：只返回 `Document.kind` 在其中的命中；IVY 的旧文档 `kind` 为空，只在不传 `kinds` 时返回。
3. MCP 暴露 `list_changes`，并把 `kinds` 参数加进 `search_docs` 的工具描述；`version` 工具返回的版本号随之更新；工具清单测试相应调整（`tests/test_mcp_server.py` 允许为此修改）。
4. 新测试覆盖参数校验、过滤、出处、零命中；`tests/test_mcp_server_threads.py` 和 `tests/analysis` 必须仍然通过。

# 不做
- 不加 `search_notices`（KKJ 已移出本版本）。
