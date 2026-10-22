# DATUMOJ-TACHIBANA-702 完成说明

## 修改文件

- `skills/datumoj/SKILL.md`：保留原有 7 条规则与 3 个示例，补充断言级别和关系断言、政策网页与 PDF、目录与统计取证、变化问答规则。
- `tests/test_skill_rules.py`：检查新增规则的关键要求，并检查 IVY 既有规则的关键条款仍存在。
- `results/DATUMOJ-TACHIBANA-702/test-outputs.log`：记录三条验收命令的输出。
- `results/DATUMOJ-TACHIBANA-702/summary.md`：本完成说明。

## 方案

将新增指引放在原有七条规则之后，避免改写或弱化 IVY 原文，也保留原测试要求的七条编号规则和示例。新增测试只检查规则文本，不依赖联网或新增依赖。

## 验收

- `make check T=tests/test_skill_rules.py`：通过，1 passed。
- `make check T=tests/eval/test_questions_behavior.py`：通过，7 passed。
- `make check T=tests/test_mcp_server.py`：通过，15 passed。

三次运行均显示沙箱中 `uv` 不可用，`make check` 按项目约定改用 `/Users/aaron/workspace/datumoj/.venv/bin/python`。

## 未核实与人工评审

当前工作树的 `search_docs` 参数只有 `queries`、`k`、`sources`、`since`，没有 `kinds`；MCP 工具注册表也没有 `list_changes`。规则按任务要求说明 `kinds` 在工具支持时使用，并在 `list_changes` 不可用或失败时禁止把无法核实说成没有变化。此工作仅修改规则和测试，未改工具实现；因此这两个能力是否已在目标宿主环境提供，尚未验证。请人工评审时核对该宿主工具版本。

未运行超出任务列出的其他测试。没有新增依赖；本任务未涉及来源内容处理、许可或安全行为变更。

跳过：工具实现与端到端宿主调用检查（不在 scope）。风险：若宿主未提供 `search_docs.kinds` 或 `list_changes`，对应问答规则无法按工具调用执行；需要人工确认宿主接口版本。
