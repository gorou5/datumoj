---
id: DATUMOJ-MAPLE-122
title: cloud run 时间预算覆盖整轮（单来源抓取截止、给发布留余量）并逐来源输出进度行
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/cli.py", "tests/test_cloud_run.py", "tests/regressions/test_audit_122.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-122/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/**", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-122/summary.md 完成说明"]
  review: {by: other_family, checklist: ["正在跑的来源是否会在整轮预算到点时停下，而不是跑完才检查", "是否给发布和收尾留了固定余量", "进度行是否只含来源 id、阶段、耗时、计数和失败类别，没有 URL、路径、异常文本或任何配置值", "被截止的来源是否记为未完成、下一轮仍会到期，而不是记成成功", "结果 JSON 的已有字段是否不变", "是否改了 core、爬虫或解析器", "是否越界"]}
  human: sampled
max_attempts: 3
---
# 目标
一次 `rikolto cloud run` 在 GitHub Actions 的作业时限内一定能跑完并发布。运行中按来源输出进度行，即使作业被外部取消，日志里也看得出跑到了哪里。

# 背景
2026-11-01 第一次真实 Cloud run（R2，从空开始），参数 `--max-minutes 40`，作业时限 60 分钟：
- 15:46 开始。8 个来源依次跑完：`jp-bodik` 用了 20 分钟，`jp-egov-data` 在第 35 分钟开始、跑了 17 分钟。
- 16:38 开始发布，16:43 第 1 代写入完成。随后的收尾（周报、释放租约）没做完，16:45 作业到 60 分钟被取消。
- 结果 JSON 只在结束时打印，日志里什么都没有；写入租约没释放；17 个来源里还有 9 个没轮到。

原因有两个：
- `run()` 里的 `max_minutes` 只在两个来源之间检查。一个来源开始以后要跑多久没有上限，只受它自己的 `scope.max_run_seconds`（默认 900 秒，只管抓取）约束，解析和建索引不计。
- `_quiet()` 吞掉了全部 stdout、stderr 和日志。这是有意的（防止来源文本和配置外泄），但也把进度一起吞掉了。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- `core/`（包括 `runner.py`、`schedule.py`）和 `testing/` 不改。抓取截止通过给 spec 的副本设置 `scope.max_run_seconds` 实现（`spec.model_copy(update={"scope": {...}})`），不改 runner 的接口。
- 结果 JSON 的现有字段和含义不变，只允许新增字段。
- 测试全部离线，沿用 `tests/test_cloud_run.py` 现有的 `world` 夹具和假时钟。不新增依赖。仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **整轮预算**：`max_minutes` 表示从开始到"开始发布"的时间上限，发布和收尾另外留出 `reserve_minutes`。
   - CLI 新增 `--reserve-minutes`，默认 10；工作流的作业时限由主对话另改，不在本任务里。
   - 每个来源开始前计算剩余时间，不足 1 分钟就不开始，并设 `budget_exhausted`（现有行为）。
   - 开始的来源，抓取截止时间取 `min(原 scope.max_run_seconds 或默认值, 剩余秒数)`，用 spec 副本传进 `run_crawl`。
   - 解析和建索引不能中途截断（会写半张表），所以抓取截止后照常解析已抓到的快照。在 summary 里写明：最坏情况的超出量等于一个来源的解析时间。
2. **被截止的来源**：抓取因 `deadline` 停下（`report.stop_reason == "deadline"`）时：
   - 本轮记录的 `stop_reason` 写 `"deadline"`；
   - 状态和退出码的判定方式不变，但这种情况不能被调度器当成本周期已成功、从而下一轮不再到期。
   - 先读 `core/schedule.py` 的到期判断再决定写法（不改 core），写进 summary。
3. **进度行**：每个来源开始和结束时，向**真正的** stderr（`sys.__stderr__`，绕过 `_quiet` 的重定向）写一行 JSON，并立即 flush。
   - 字段只有：`event`（`source_start` / `source_end` / `publish_start` / `publish_end` / `round_end`）、`source_id`、`elapsed_s`（自本轮开始）、`new`、`unchanged`、`parsed`、`failures`（只放类别名和计数）、`stop_reason`。
   - 不写 URL、路径、异常文本或任何环境变量的值。
   - 发布开始和结束各写一行；结束时照旧打印完整结果 JSON。
4. **测试**（`tests/test_cloud_run.py`，新增 `tests/regressions/test_audit_122.py`）：
   - 假时钟下，第一个来源抓取期间时间耗尽：它的抓取截止秒数等于剩余时间；后面的来源不开始；发布照常进行。
   - 被截止的来源下一轮仍然到期。
   - 剩余不足 1 分钟时不开始新来源。
   - `reserve_minutes` 从 CLI 传到 `run()`。
   - 进度行：每行都是合法 JSON、只含允许的键；在某个来源的抓取里抛出一个含假 URL、假路径和假密钥的异常，断言 stderr 里一个都没有出现。
   - 已有的结果字段断言全部不变。
5. `docs/deploy/runners.md` 加一小段：预算含义（抓取预算 + 发布余量），以及作业时限应不小于 `max_minutes + reserve_minutes + 一个来源的解析时间`。
6. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不改 `.github/workflows/`（作业时限由主对话另提 PR 调整）。
- 不改 runner、调度器、各来源的爬虫和解析器。
- 不处理本轮单个来源的失败（`eu-eurostat`、`jp-abr` 抓取失败，`eu-data-europa`、`intl-wdi`、`jp-bodik` 部分解析失败）；这些由主对话另查。
