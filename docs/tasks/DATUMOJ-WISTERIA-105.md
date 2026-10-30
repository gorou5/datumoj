---
id: DATUMOJ-WISTERIA-105
title: safe_reason 白名单补上参数校验和查询错误的固定提示：按"精确类型 + 模板全匹配"放行，MCP 调用方重新看得到怎么改参数
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-104"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/errors.py", "tests/core/test_errors.py", "tests/test_mcp_server.py", "tests/test_mcp_server_threads.py", "tests/regressions/test_audit_wisteria_105.py", "results/DATUMOJ-WISTERIA-105/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "sources/**", "schemas/**", "eval/**", ".github/**", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/analysis/**", "src/datumoj/rikolto/store/**", "src/datumoj/rikolto/mcp_server.py", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_105.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_104.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-105/summary.md 完成说明（含新增的每条模板、它对应的抛出位置、占位符允许的字符）"]
  review: {by: other_family, checklist: ["放行是否只按精确类型加模板全匹配，不按前缀或包含", "占位符是否只允许参数名、数字等安全字符，不能放进路径、SQL 原文和来源文字", "DuckDB 透传的消息是否仍不放行", "104 的回归测试是否原样通过", "只改了 errors.py 和测试，没有改抛出位置", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
104 之后 MCP 对所有异常只返回类型名，调用方看不到"since 必须带时区""结果超过 N 行，请加条件或聚合"这类告诉它怎么改参数的提示。2026-10-30 人定：把项目自己写死的参数校验和查询错误提示加进 `safe_reason` 白名单。

# 背景
- `core/errors.py` 的 `safe_reason` 现在只放行两类：`BudgetExceeded` 的 4 个预算码、`ParseError` 的 5 条固定文案，都按"精确类型 + 消息全等"匹配。
- 要放行的提示都是代码里写死的模板，只有参数名或数字是变量，例如（不限于，以实际代码为准）：
  - `analysis/tools.py` 的参数校验（`ValueError`）：`{name} 必须是 {lo}–{hi} 的整数`、`{name} 必须是带时区的 ISO-8601 日期时间或 YYYY-MM-DD`、`{name} 必须带时区`、`since 不能晚于 until`、`kinds 只能包含 …`、`invalid JSON object` 等；
  - 查询：`QueryTimeout` 的 `query_sql 整个请求超过 {n} 秒`、`查询超过 {n} 秒`；`TooManyRows` 的 `结果超过 {n} 行，请加条件或聚合`；`QueryRejected` 的 `只允许单条 SELECT / WITH 查询`、`不允许多条语句`、`不允许使用 {name!r}（读文件或执行 SQL 串）`；`QueryError` 的 `发布感知读取需要持久化 SnapshotOrder`、`历史段引用无效` 等固定文案。
- **不放行**：`store/observations.py` 里把 DuckDB 原文透传的 `QueryError(str(exc))`、`SQL 无法解析：{exc}`、`查询试图访问外部资源：{exc}`。DuckDB 的消息可能带字面量或路径。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 只改 `core/errors.py` 和测试；抛出位置（`analysis/`、`store/`、`mcp_server.py`）和契约一律不改。
- `tests/regressions/**` 里已有的文件不许改，104 的回归测试必须原样通过。其他单元测试只允许把"只剩类型名"的断言改成新放行的完整提示，每处列进 `summary.md`。
- 测试全部离线；仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **模板白名单**：在 `errors.py` 里按 `(精确类型, 正则模板)` 列出，用全匹配（`fullmatch`）判断；类型仍按 `type(exc) is X` 精确比较，子类不放行。
2. **占位符要窄**：参数名只允许 `[A-Za-z_][A-Za-z0-9_]{0,63}`，数字只允许整数或小数；枚举列表只允许小写字母、数字、下划线、逗号和空格。不允许任何占位符匹配到 `/`、`\`、引号以外的 SQL 文本或换行。
3. **输出格式**：放行的提示沿用 104 的写法 `类型名: 消息`；预算码保持原格式；长度上限和去控制字符不变。
4. **DuckDB 透传仍只留类型名**。
5. **回归测试** `test_audit_wisteria_105.py` 覆盖：
   - 每条新模板各一个放行用例；
   - 同类型但消息里塞进路径、SQL 片段、换行时不放行；
   - 子类不放行；
   - DuckDB 透传的 `QueryError` 仍只有类型名；
   - 通过 MCP 调用一个参数错误的工具，返回里能看到完整提示。
6. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不改抛出位置和文案，不加新的异常类型，不改契约。
- 不放开 DuckDB 原文，不改本机终端的输出。
