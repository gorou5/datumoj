---
id: DATUMOJ-IVY-604
title: MCP 服务器（stdio）
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-603"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/mcp_server.py", "tests/test_mcp_server.py", "results/DATUMOJ-IVY-604/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_mcp_server.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-604/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
在 Claude Code / Claude Desktop 里注册后，能调用 DATUMOJ 的全部只读工具。

# 背景
开发线：F 入口线。用 `mcp` Python SDK（FastMCP），只做协议包装。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-604/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 暴露工具：`search_docs`、`search_catalog`、`query_sql`、`get_chunk`、`cite`、`version`、`coverage`；描述用英文写清用途、参数和限制（例如“数值只能用 query_sql”）。
2. 暴露 prompt `datumoj_answer_rules`：读取 `skills/datumoj/SKILL.md`，不存在时返回内置的简短版本。
3. 入口：`python -m datumoj.mcp_server`（CLI 子命令由集成任务接上）。
4. 异常转成 MCP 错误结果，服务器不崩溃。
5. 测试用 SDK 的内存会话和假实现调用每个工具一次，并验证错误处理。

# 不做
- 不改 scope 以外的文件。
