---
id: DATUMOJ-TACHIBANA-602
title: 每周变化报告：datumoj report weekly 生成带出处的 Markdown
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-601", "DATUMOJ-TACHIBANA-102", "DATUMOJ-TACHIBANA-403"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/analysis/reports.py", "src/datumoj/cli.py", "tests/analysis/test_weekly_report.py", "results/DATUMOJ-TACHIBANA-602/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_cli.py"]
acceptance:
  automated:
    - cmd: make check T=tests/analysis/test_weekly_report.py
    - cmd: make check T=tests/test_cli.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-602/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
每周能在 `data/reports/` 生成一份 Markdown，人一眼看出这周资料库里变了什么、哪里出了问题。

# 背景
开发线：F 入口线。计划 6.4 节和第 3 节第 4 条。变化事件来自 601 的变化日志；提醒来自 102 的 `core/monitor.py`；出处用契约的 `Citation.render()`。按 PLAN 第 13 节第 1 条，报告用模板生成，不调用 LLM。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-602/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `analysis/reports.py`：`weekly_report(deps, since, until, alerts) -> str`，按来源分节，每节列新增、修改、删除、抓取失败、解析失败（按 `ChangeKind` 分组，各自计数），每条一行：文档标题（取得到时）、`summary`、出处。
2. 出处（2026-10-22 人定，不改契约）：事件有快照（`new_sha256`，删除类用 `old_sha256`）且 manifest 里能找到该快照时，用契约的 Citation 规则生成并输出 `Citation.render()` 的一行文字；事件没有 `locator` 时按整份文档引用（locator 的写法与现有 `cite` 能接受的一致，写清做法）。没有快照或在 manifest 里找不到快照的事件（例如首次抓取就失败的 `fetch_failed`），不拼造出处，改为输出 `resource_url`（或 `doc_key`）、`detected_at` 和失败类型，并标明“没有快照，无法还原”。不得伪造哈希或 locator。
3. 提醒（2026-10-22 人定）：报告模块不读取调度器的运行记录或开关文件；`alerts` 由调用方传入（`core/monitor.py` 的 `Alert` 列表，或 `Scheduler.status()` 各来源 `alerts` 汇总后的同样内容）。CLI 从 `Scheduler.status()` 取出提醒传给 `weekly_report`。报告末尾列出这些提醒，以及“本周有缺口的来源”（其中 `retention_gap` 规则的提醒）。
4. `needs_ocr`（2026-10-22 人定）：只统计**本周有变化事件**的 PDF（按事件的 `doc_key` 取文档 metadata 的 `needs_ocr_pages` 和 `pages`），不统计全库；报告里写明统计口径是“本周有变化的 PDF”。
5. 报告开头是总览表（每个来源各类事件的数量）。
6. CLI：`datumoj report weekly [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--out PATH]`，默认最近 7 天、写到 `<data_dir>/reports/weekly-<ISO 年>-W<周>.md`，并打印路径。
7. 没有任何变化时也生成报告，写明“本周没有变化”。来源正文的片段不直接拼进 Markdown 标题等结构里（避免注入），一律作为普通文本并转义 Markdown 特殊字符。
8. 新测试用假的变化日志和假提醒生成报告并逐节断言；`tests/test_cli.py` 必须仍然通过。

# 不做
- 不做主动推送。
- 不调用 LLM 做摘要。
