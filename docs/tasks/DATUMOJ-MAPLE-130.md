---
id: DATUMOJ-MAPLE-130
title: 来源连续失败自动开 GitHub issue（source-broken），恢复成功后自动关闭；结果 JSON 新增 source_health
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/cloud.py", ".github/workflows/cloud-run.yml", "scripts/source_issues.py", "tests/test_cloud_run.py", "tests/test_cloud_workflow.py", "tests/test_source_issues.py", "tests/regressions/test_audit_122.py", "tests/regressions/test_audit_130.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-130/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/workflows/cloud-embed.yml", ".github/workflows/ci.yml", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/rikolto/pipeline.py", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_cloud_workflow.py
    - cmd: make check T=tests/test_source_issues.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-130/summary.md 完成说明"]
  review: {by: other_family, checklist: ["连续失败轮数是否从运行账本正确计算，deadline 停下是否不算失败", "失败类别是否只含固定类别和计数，不含 URL、密钥、正文、异常原文", "issue 标题、正文、评论是否只来自白名单字段", "连续失败 2 轮才开 issue、已有同名 issue 是否追加评论而不重复开", "来源恢复成功时是否留言并关闭对应 issue", "开关 issue 的步骤是否在发布之后、出错不影响本轮结果与发布", "工作流权限是否只加了 issues: write，其余不变；fork 不执行", "是否改了 core、pipeline、爬虫或越界"]}
  human: sampled
max_attempts: 5
---
# 目标
来源因为接口变更等原因持续失败时，自动在 GitHub 上开 issue 推动修复；修好、再次成功后自动关闭。不靠人翻 Actions 日志。

# 背景
2026-11-04 人决定开始一周试运行，"修好一个算一个"，并要求"接口变更导致抓取失败时要有机制推动修复"。
- 现状：`core/monitor.evaluate_cloud_alerts` 能算出"超过一个调度周期没有成功运行"（`cloud_run_missing`），但只写进每周报告，没人会被提醒。
- 云端结果对每个来源只记 `crawl_failed` / `parse_index_failed` 等总数，看不出原因：
  - 抓取：`CrawlReport.failures` 本来按结果类型计数（`FetchOutcome` 的值），`cloud._source` 把它们加总成一个数；
  - 解析：`ParseSummary` 里有每个快照的错误原因（如 `ParseError: empty_content: ...`），云端只记数量。
- 近几轮的失败：jp-stat-dashboard、eu-eurostat、us-fedreg 抓取失败；eu-data-europa、jp-bodik、jp-estat-api、jp-egov-law 有解析失败。
- 仓库和 issue 都是公开的。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 不改 core、pipeline、爬虫、解析器、索引；只改 `cloud.py`、`cloud-run.yml`，新增一个小脚本。
- 测试全部离线：账本用现有夹具，`gh` 用假的可执行文件（写进临时目录、放到 PATH 前面，记录收到的参数）。不新增依赖。不写账号、密钥、本机绝对路径。

# 要求
1. **结果 JSON 新增 `source_health`**（`cloud.py`）：列表，每项一个来源，包括本轮跑过的来源，以及本轮触发 `cloud_run_missing` 告警的来源。字段只能是：
   - `source_id`、`status`（`ok` / `failed` / `deadline` / `missing`）、`phase`（`crawl` / `parse_index` / `embedding` / `schedule` / null）；
   - `categories`：类别 → 计数。抓取用 `FetchOutcome` 的值；解析取错误原因里 `ParseError: <code>:` 的 `<code>`（只允许 `[a-z_]{1,40}`，否则记 `other`），非 ParseError 的记异常类名；
   - `consecutive_failures`：从运行账本按时间倒序数该来源连续失败的最终记录数；`deadline` 停下且没有失败的记录不算失败，也不打断计数以外的语义（遇到它停止计数）；
   - `first_failure_at`：这串连续失败里最早一次的开始时间（ISO 字符串），没有就 null。
   - 不得包含 URL、正文、异常原文。原有字段和含义不变。
2. **开关 issue 的脚本** `scripts/source_issues.py`：
   - 输入：结果 JSON 文件路径、阈值（默认 2）；通过 `gh` 命令行操作当前仓库的 issue（`GH_REPO`、`GH_TOKEN` 由工作流提供）。
   - `consecutive_failures >= 阈值` 或 `status == "missing"`：找标签 `source-broken`、标题以 `[source-broken] <source_id>` 开头的开着的 issue；没有就新建（标签不存在时先创建），有就追加一条评论。
   - `status == "ok"` 且有对应开着的 issue：评论"已恢复（第 N 代）"后关闭。
   - 标题、正文、评论只用 `source_health` 的白名单字段和代次号；写一个函数统一生成文字，测试断言里面不含 `http`、`://`、`?` 后的参数等。
   - 任何一个 `gh` 调用失败只打印脱敏的失败计数，不抛出到退出码以外；脚本整体失败也不能影响本轮发布（由工作流保证）。
3. **工作流**（`cloud-run.yml`）：
   - `permissions` 增加 `issues: write`，其余不变；fork 不执行（沿用现有 `if`）。
   - 运行步骤把结果 JSON 写到 `$RUNNER_TEMP` 下的文件，同时照常输出到日志；运行失败时下一步仍执行（`if: always()` 且结果文件存在）。
   - 新增步骤在运行步骤之后调用脚本，`continue-on-error: true`。不改 `cloud-embed.yml`。
4. **测试**：
   - `tests/test_cloud_run.py`：`source_health` 的字段白名单；连续失败计数（失败、失败、成功 → 只数到成功为止）；deadline 不算失败；解析错误代码提取与 `other`；`missing` 来源出现在列表里。
   - `tests/test_source_issues.py`：用假 `gh` 验证新建、追加评论、恢复关闭、阈值以下不动作、`gh` 失败时不抛出；生成的文字不含 URL。
   - `tests/test_cloud_workflow.py`：权限只多了 `issues: write`；新步骤在运行步骤之后、`continue-on-error`；结果文件写在 `$RUNNER_TEMP`。
   - 新增 `tests/regressions/test_audit_130.py`：复现"来源连续两轮抓取 HTTP 错误 → 开 issue → 修好后一轮成功 → 关闭"的完整流程（用假 `gh`）。
   - 已有测试和断言全部不变；如 `test_audit_122` 的结果字段白名单因新增 `source_health` 要改，只加这个键，在 summary 里说明。
5. `docs/deploy/runners.md` 补一段：`source_health` 的字段、开关 issue 的规则和阈值、issue 只含哪些信息。
6. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不改 core、pipeline、爬虫、解析器，不改每周报告和告警规则本身。
- 不改 `cloud-embed.yml`，不加定时触发（另做）。
- 不自动修复来源，不自动写任务文件。
