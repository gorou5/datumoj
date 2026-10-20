# DATUMOJ-IVY-604 完成说明

## 变更文件
- `src/datumoj/mcp_server.py`（新）
- `tests/test_mcp_server.py`（新）
- `results/DATUMOJ-IVY-604/summary.md`（新）

## 做了什么
- `create_server(tools: AnalysisTools) -> MCPServer`：注册 7 个工具（`search_docs`、`search_catalog`、`query_sql`、`get_chunk`、`cite`、`version`、`coverage`），英文描述写明用途、参数、限制（数值只能用 `query_sql` 等），只转调 603 的 `AnalysisTools`。
- prompt `datumoj_answer_rules`：读 `skills/datumoj/SKILL.md`，缺失、读不了或为空时返回内置简短版（`FALLBACK_RULES`）。
- 错误：每个工具包一层 `_guarded`，任何异常转成 `ToolError("类型: 消息")`，SDK 返回 `isError` 结果，服务器继续运行。
- 入口 `python -m datumoj.mcp_server`：工具层由环境变量 `DATUMOJ_TOOLS_FACTORY=包.模块:可调用对象`（无参，返回 `AnalysisTools`）提供；没配置时退出并给出说明。

## 验收
- `make check T=tests/test_mcp_server.py`：15 passed。
- 测试用 `mcp.Client` 内存会话 + `datumoj.testing` 假实现，每个工具调用一次，另测工具列表与描述、5 种错误（SQL 被拒、空 SQL、k 越界、sha256 格式错、参数类型错）后服务器仍可用、prompt 的文件/回退两条路径、未配置工厂时的退出。无联网。

## 需要注意
- 锁定的 `mcp` 是 2.3.0：`FastMCP` 已改名为 `mcp.server.mcpserver.MCPServer`，代码按新名字写（旧的 `mcp.server.fastmcp` 导入会直接报错）。
- 没有真实接线：604 的 `depends_on` 里没有 wiring，所以用环境变量工厂代替。集成任务 903 接上 `datumoj mcp` 时应改为直接调用 `create_server(真实 tools)`，届时可去掉环境变量机制。
- `skills/datumoj/SKILL.md` 目前不存在（只有 `.gitkeep`），所以现在 prompt 返回的是内置版本。
- 没做：stdio 子进程级别的端到端测试（只测了内存会话）。
