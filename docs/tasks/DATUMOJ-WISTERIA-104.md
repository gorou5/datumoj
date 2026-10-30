---
id: DATUMOJ-WISTERIA-104
title: 异常信息统一：落库和对外输出的失败原因只留异常类型和白名单消息；逐处审 blind-except
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-103"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/**", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/eval.py", "src/datumoj/eltiro/executor.py", "src/datumoj/eltiro/providers.py", "pyproject.toml", "tests/**", "results/DATUMOJ-WISTERIA-104/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", ".github/**", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/contracts.py"]
protected: ["tests/fixtures/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_104.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-104/summary.md 完成说明（含 49 处 blind-except 逐处的处理：保留 / 收窄 / 改用 safe_reason，以及白名单的组成）"]
  review: {by: other_family, checklist: ["写进 manifest、状态库、日志、命令行输出的失败原因是否都经过 safe_reason", "白名单之外的异常消息是否不再落库或输出", "保留的 blind-except 是否都有一行理由", "收窄的 except 是否没有漏掉原来要兜住的错误", "已有回归测试是否原样通过", "BLE001 的按文件 ignore 是否删掉", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
2026-10-29 外部审查发现：`pipeline.py` 等处在 `except Exception` 后把 `f"{type(exc).__name__}: {exc}"` 写进 manifest 的失败原因；另一处（`_maintain_vectors`）又特意不记消息，理由是"SDK 异常可能含路径、配置或来源文本"。两种做法不一致，失败原因里可能出现本机路径或来源原文。本任务统一成一个出口，并逐处审 49 处 blind-except。

# 背景
- 写失败原因的地方（不限于）：`pipeline.py`（约 183、281、614、672、884 行）、`core/runner.py`、`core/schedule.py`、`cli.py`、`admin/web.py`、`mcp_server.py`、各解析器。
- 现在本机 manifest 的 `publish_states.error` 里没有路径（只读核对，0 条），所以这是加固，不是事故修复。
- 103 把 `BLE001` 按文件临时 ignore 了，注释写明由本任务处理。
- Eltiro 的 `cli.py` 已经统一走 `_error()` 固定输出，只需确认、不重写。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 契约文件不改；需要改契约时写进 `summary.md` 并停下来问。
- 已有回归测试不许改，必须原样通过。如果某条断言依赖原来的异常消息文字，写进 `summary.md` 并停下来问。
- 测试全部离线，临时文件放 `tmp_path`。
- 仓库是公开的：不写账号、密钥、本机绝对路径。

# 要求
1. **一个出口**：在 Rikolto 的公共位置加 `safe_reason(exc) -> str`：
   - 输出异常类型名；
   - 消息只在白名单内才保留：项目自己定义的、消息由代码常量组成的异常（例如预算、限速、robots、HTTP 状态码这类）；
   - 其他异常（含第三方 SDK、`OSError`、解析库异常）只留类型名；
   - 长度上限，去掉换行和控制字符。
2. **替换**：所有把异常消息写进 manifest、状态库、调度日志、管理页面、MCP 返回、命令行输出的地方都改用 `safe_reason`。只写进本机调试日志（`_LOG.debug`）的可以保留原消息。
3. **逐处审 blind-except**：49 处每处三选一：保留（加一行理由的 `noqa`）、收窄到具体异常、或保留但改用 `safe_reason`。逐处结果列进 `summary.md`。删掉 103 留下的 BLE001 按文件 ignore，`make lint` 仍为 0 条。
4. **回归测试** `test_audit_wisteria_104.py`：
   - 异常消息里带路径、来源原文标记字符串时，manifest、调度日志、命令行输出里都不出现；
   - 白名单异常的消息照常保留；
   - 一个快照失败仍不影响其他快照（原有的隔离不变）。
5. `make check T=tests` 全部通过。

# 不做
- 不拆复杂函数，不改重试和隔离的逻辑。
- 不改契约和 schema。

# 重做要求（2026-10-30，前两次尝试在开头停下后人定）
前两次尝试都按"断言依赖原消息就停下来问"停了，没有改代码。人已经定了口径，按下面做，不用再停：
1. **本机命令行终端输出保留完整消息**：`rikolto` 命令行打印到终端的"运行失败：<类型>: <消息>"维持原样，`tests/test_cli.py` 里 `运行失败：RuntimeError: storage unavailable` 的断言不改。Eltiro 的 `cli.py` 已经是固定输出，不改。
2. **落库和共享出口一律走 `safe_reason`**：manifest、状态库、调度记录和调度日志（含 `stop_reason`）、管理页面、MCP 返回。所以 `tests/core/test_schedule.py` 里 `stop_reason == "storage failed"` 这类断言要改成新格式（`OSError` 不在白名单，只留类型名）。
3. **白名单消息保持原样**：预算的 `budget_exceeded:output_chunks` 这类由代码常量组成的原因字符串按原格式保留，不加类型名前缀；`tests/core/test_budget.py` 的断言不应需要改。
4. **哪些测试能改**：`tests/regressions/**` 一律不许改（回归测试原样通过）。`tests/` 下其他单元测试只允许改因第 2 条格式变化而失效的断言，每处列进 `summary.md`；如果还有其他不在上面范围的测试需要改，写进 `summary.md` 并停下来问。
5. **`pyproject.toml` 可以改**：只允许删掉或缩小 103 留下的 `BLE001`、`S110`、`S112` 按文件 ignore；其他配置不动。
6. **回归测试文件要新建**：`tests/regressions/test_audit_wisteria_104.py` 由本任务新建（前两次因为没建而验收失败）。

# 重做要求（2026-10-30 第二轮，按上一轮人定的口径放宽范围）
上一轮第 1 次尝试实现后有 9 个单元测试失败（断言原消息、不在 scope 内）；第 2 次因 `tests/test_mcp_server_threads.py` 不在 scope 而停下。口径已定，不用再问：
7. **测试范围**：`tests/**` 都在 scope 内，但 `tests/regressions/**` 里已有的文件仍一律不许改（只新建 `test_audit_wisteria_104.py`）。其他单元测试只允许改因第 2 条格式变化而失效的断言，比如把完整消息改成只留类型名；不许删测试、不许放宽与格式无关的断言。每处改动列进 `summary.md`。已知的有：`tests/core/test_http.py`、`tests/core/test_run_ledger.py`（管理页面）、`tests/test_mcp_server_threads.py`（MCP 返回按第 2 条只留类型名）、`tests/test_pipeline_retry.py`、`tests/test_pipeline_vectors.py`（`status`/`parse` 输出的向量缺失原因来自落库记录，按第 2 条处理）。
8. **Eltiro 三个文件的 BLE001**：`eltiro/eval.py`、`executor.py`、`providers.py` 只允许把按文件 ignore 换成逐行 `# noqa: BLE001` 加一行理由，或收窄到具体异常；不改它们的错误格式和行为。换完删掉这三个文件的按文件 ignore。

