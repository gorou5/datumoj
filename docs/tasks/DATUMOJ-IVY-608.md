---
id: DATUMOJ-IVY-608
title: 修复：MCP 服务器的工具调用固定在同一个线程，SQLite 不再跨线程报错
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/mcp_server.py", "tests/test_mcp_server_threads.py", "results/DATUMOJ-IVY-608/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_mcp_server.py"]
acceptance:
  automated:
    - cmd: make check T=tests/test_mcp_server_threads.py
    - cmd: make check T=tests/test_mcp_server.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-608/summary.md 完成说明"]
  review: {by: other_family, checklist: ["工具层对象是否在执行工具调用的那个线程里创建", "是否用真实 SQLite 存储验证，而不是只用假实现", "并发调用是否按顺序执行、不死锁、异常能正常返回", "原有 604 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
用真实的 SQLite 存储启动 MCP 服务器时，`search_docs`、`cite` 等工具能正常执行，不再报 `SQLite objects created in a thread can only be used in that same thread`。

# 背景
开发线：F 入口线（修复 DATUMOJ-IVY-604）。集成任务 DATUMOJ-IVY-903 用真实存储跑 MCP 内存会话时，`search_docs` 和 `cite` 都失败了：存储在构建工具层的线程里打开 SQLite 连接，MCP SDK 却在工作线程里调用同步工具。604 的测试用的是假存储，所以没发现。MCP 是 Claude 使用资料库的入口，这个问题会让它在真实环境里完全不可用。

计划见 `docs/PLAN.md`；测试全部离线，不能改依赖和契约，不改各存储模块。

# 要求
1. `create_server` 改为接收一个无参工厂（`Callable[[], AnalysisTools]`），也继续兼容直接传入 `AnalysisTools` 实例（兼容 604 的现有用法和测试）。
2. 服务器内部有一个**专用工作线程**（例如单线程的 `ThreadPoolExecutor`）：工厂在这个线程里调用，构建出的工具层和其中所有存储都只在这个线程里使用；每个工具调用都提交到这个线程执行，异步等待结果。直接传入实例时，同样把调用串行到一个固定线程，并在文档里说明实例应在该线程中创建。
3. `main()` 把环境变量指定的工厂交给 `create_server`，不再在主线程里先构建工具层。
4. 原有 `_guarded` 的错误处理保留：工具内异常转成 MCP 错误结果，服务器不崩溃。
5. 原有 `tests/test_mcp_server.py` 已列为 protected，必须全部通过。
6. 新测试 `tests/test_mcp_server_threads.py`：用真实的 `SqliteChunkStore`、`SqliteKeywordIndex`、`SqliteManifest`、`FileRawStore`（在 `tmp_path` 里建一个小语料），通过 MCP 内存会话调用 `search_docs` 和 `cite`，断言成功且 `cite` 能还原原文；并发发起多个调用也都成功；工具抛异常时返回错误结果。

# 不做
- 不改存储模块（不给 SQLite 加 `check_same_thread=False` 之类的全局改动）、不改工具层、契约。
