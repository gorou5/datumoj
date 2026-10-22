# DATUMOJ-TACHIBANA-603 完成说明

实现和测试已写入任务 scope。默认生产接线还缺一项 scope 外改动，见“待确认”。

## 改动

- `src/datumoj/analysis/tools.py`：新增 `ChangeLog` 契约注入和 `list_changes`。默认查询最近 7 天，校验时间、来源、事件类型和 `limit`；新增/修改取新快照，删除取旧快照；事件摘要和文档标题以 `SourceText` 返回。按文档类型筛选 `search_docs`，未传 `kinds` 时保留旧文档 `kind=None`。
- `src/datumoj/mcp_server.py`：暴露 `list_changes`，给 `search_docs` 增加 `kinds` 参数说明；MCP `version` 返回带 `+tachibana603` 标记的构建版本，包基础版本保持原值。
- `tests/analysis/test_list_changes.py`：覆盖默认七天窗口、参数校验、来源/事件类型过滤、零命中、新旧快照出处、文档类型过滤和旧文档兼容。
- `tests/test_mcp_server.py`：更新工具清单，检查工具说明、`list_changes` 调用、`kinds` 传递和 MCP 版本标记。
- 测试输出和变更文件清单见本目录的 `acceptance.log`、`changed-files.txt`。

实现只依赖 `ChangeLog` 契约及模型，没有导入 TACHIBANA 其他实现或增加依赖。文档正文仍被当作数据并包在 `SourceText` 中。无法从快照和块存储还原 locator、且事件本身没有 locator 时，出处为 `null` 并发出 warning；不会编造 locator。

## 验收

- `make check T=tests/analysis/test_list_changes.py`：17 passed。
- `make check T=tests/test_mcp_server.py`：16 passed。
- `make check T=tests/test_mcp_server_threads.py`：6 passed。
- `make check T=tests/analysis`：80 passed。
- 补充 `make check T=tests/core/test_contracts_tachibana.py`：342 passed。
- `git diff --check`：通过。测试全部使用假实现，没有联网。

`make check` 按项目配置在 `uv` 不可用时改用 `/Users/aaron/workspace/datumoj/.venv/bin/python`；测试正常通过。未运行 Ruff，因为当前环境没有 `ruff` 命令。

## 待确认

1. 601 的 `wiring.py` 创建了 `change_log`，但 `wiring.build()` 构造 `AnalysisTools` 时没有传入它；`wiring.py` 不在本任务 scope。当前标准 MCP 工厂因此会把变化查询报告为 `not_ingested`，即使流水线已写入事件。要让 Claude 的默认连接真正返回变化，需要将 `src/datumoj/wiring.py` 加入后续任务 scope 并传入现有 `wiring.change_log`。
2. `ChangeEvent.locator` 可为空，但 `Citation.locator` 必填。对没有文档块且事件没有 locator 的事件（例如部分解析失败事件），当前模型无法构造可还原出处。若要求所有事件都带非空 citation，需要先由契约明确快照级 citation 的 locator 语义或让事件保证 locator；本任务未改受保护的契约。

## 风险与未核对项

默认 wiring 未传日志、缺少 locator 的事件出处以及 Ruff 检查尚未验证；前两项限制实际端到端行为。MCP 版本标记仅用于区分本任务工具构建，项目包版本字段没有在 scope 外修改。

跳过了 scope 外的 wiring 和模型契约修改；未验证默认 MCP 工厂能读到 601 日志。未发现测试联网或依赖变更。
