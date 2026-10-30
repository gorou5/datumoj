---
id: DATUMOJ-MAPLE-602
title: 0.2 遗留修复（CodeRabbit #15）：短保留来源改为显式标记；去掉 MCP version 的残留后缀
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: codex
model: null
tier: low
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/monitor.py", "src/datumoj/mcp_server.py", "sources/jp-jma.yaml", "tests/core/test_monitor.py", "tests/test_mcp_server.py", "tests/integration/test_tachibana_m3.py", "results/DATUMOJ-MAPLE-602/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_monitor.py
    - cmd: make check T=tests/test_mcp_server.py
    - cmd: make check T=tests/integration/test_tachibana_m3.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-602/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
只有真正的短保留来源（JMA）才会报“保留缺口”，jp-gov-web、jp-stat-dashboard 正常每天运行时不再误报；MCP `version` 不再带 `+tachibana603`。

# 背景
开发线：F 监控与遗留。CodeRabbit 在 #15 指出：`core/monitor.py` 把 `retention_note` 非空的来源都当成短保留来源（还写死了 `spec.id == "jp-jma"`），而 jp-gov-web、jp-stat-dashboard 的 `retention_note` 只是说明文字；关闭的来源也会报尾部缺口。`mcp_server.py` 的 `version` 工具在版本号后拼了 `+tachibana603`，`tests/test_mcp_server.py` 有对应断言。（CodeRabbit 同批指出的 `parse_pending` 全表扫描并入 MAPLE-202，`list_changes` 全表扫描并入 MAPLE-404，HTML `drop_tree()` 并入 MAPLE-203。）

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核问题的回归用例在 `tests/test_evidence_regressions.py`（MAPLE-002 写出，修好之前标 `xfail(strict=True)`，原因里写着负责的任务号）。修好本任务负责的用例后**删掉它们的 xfail 标记**，不改断言；其他任务的用例不动。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-602/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 短保留只看显式标记 `scope.short_retention: true`；去掉按 `retention_note` 和按来源 ID 的判断；`sources/jp-jma.yaml` 加上 `short_retention: true`（只加这一项）。
2. 关闭的来源（`enabled=False`）不报尾部缺口（最近一次成功到现在）；历史上两次成功之间的缺口照常报。
3. MCP `version` 返回包的真实版本号，不拼后缀；`tests/test_mcp_server.py` 的对应断言改为检查不含 `+`（或与包版本一致）。
4. `tests/core/test_monitor.py` 里用 `retention_note` 构造短保留来源的用例改用显式标记，并新增：只有 `retention_note` 不报、关闭的来源不报尾部缺口。全部已有测试（`make check T=tests`）必须仍然通过。
5. （2026-10-23 第 1、2 次尝试后补充）`tests/integration/test_tachibana_m3.py` 的 `test_weekly_report_and_monitor_alerts_match_events` 断言 jp-jma 报 `retention_gap`，但测试里 jp-jma 没有打开，按本任务的新规则（关闭的来源不报尾部缺口）不再报。允许只改这个测试：在调用 `schedule status` 之前用 `cli.scheduling.set_enabled(deps.data_dir, True)` 和 `set_enabled(deps.data_dir, True, ["jp-jma"])` 打开总开关和 jp-jma（或等价写法），保留原断言不变；不改该文件里的其他测试。

# 不做
- 不改其他来源配置。
- 不改监控的其他规则。
