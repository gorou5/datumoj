---
id: DATUMOJ-TACHIBANA-102
title: 抓取监控与提醒：连续失败、解析失败率、超期未成功、短保留来源断档
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-001"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/monitor.py", "src/datumoj/core/schedule.py", "src/datumoj/admin/web.py", "tests/core/test_monitor.py", "tests/test_admin_alerts.py", "results/DATUMOJ-TACHIBANA-102/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/core/test_schedule.py", "tests/test_admin.py", "tests/test_cli_schedule.py"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_monitor.py
    - cmd: make check T=tests/test_admin_alerts.py
    - cmd: make check T=tests/core/test_schedule.py
    - cmd: make check T=tests/test_admin.py
    - cmd: make check T=tests/test_cli_schedule.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-102/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
抓取出了问题时，`datumoj schedule status` 和管理页面（`datumoj admin`）会给出提醒，并说明失败属于哪一类（来源不可用、频率受限、认证失败、格式变化等）。

# 背景
开发线：A 抓取基础。定时、开关、运行锁、运行记录（`data/runs.jsonl`，每行含 `source_id`、`started_at`、`ended_at`、`new`、`unchanged`、`failures`（按 outcome 计数）、`stop_reason`、`exit_status`、`status`）和管理页面已由 IVY-107、610 提供，见 `core/schedule.py`、`admin/web.py`。本任务只加提醒（计划 6.3 节）。失败分类用契约的 `classify_failure`，解析统计用 `Manifest.parse_stats`，失败明细用 `Manifest.failure_summary`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-102/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 新模块 `core/monitor.py`：纯函数 `evaluate_alerts(spec, runs, manifest, now, *, enabled) -> list[Alert]`。`Alert` 含 `source_id`、`rule`、`severity`（`warning` / `error`）、`message`（中文一行）、`since`、`details`（JSON 可序列化）。规则：
2. （a）`consecutive_failures`：该来源最近 N 次运行（默认 3）都失败（`status` 不是 `completed`，或 `exit_status` 非 0）→ error；
3. （b）`parse_failure_rate`：最近 24 小时的解析失败率比过去 7 天（不含最近 24 小时）的失败率高出阈值（默认高 20 个百分点，且最近 24 小时至少 10 个快照）→ warning；
4. （c）`overdue`：来源已打开、有 `schedule`，距上次**成功**运行超过两个周期（hourly → 2 小时，daily → 48 小时；从没成功过则从打开后第一次运行算起）→ warning；关闭的来源不报；
5. （d）`retention_gap`：短保留来源（`spec.retention_note` 非空，或 `scope.short_retention: true`，JMA 属于这类）两次成功运行之间、或最近一次成功到现在超过 6 小时 → error，消息里写明缺口起止时间（缺口超过来源保留期就再也补不回来）；
6. （e）每条提醒的 `details` 里给出最近失败按 `FailureKind` 的计数和是否可重试（由 `failure_summary` 的最近失败快照经 `classify_failure` 得到）。
7. 阈值写成模块常量，`evaluate_alerts` 可以传参覆盖；时间一律可注入，测试不依赖真实时钟。
8. `Scheduler.status()` 的每个来源加 `alerts` 列表，顶层加 `alert_count`；`datumoj schedule status` 的 JSON 输出随之带上（CLI 不用改代码就该带上；若需要改 CLI，写进 summary）。
9. 管理页面在来源表格上方加“提醒”区：按严重程度排序，没有提醒时显示“没有提醒”；页面里的文字一律转义（沿用 `_e`）。
10. 新测试覆盖每条规则的触发和不触发（含边界：刚好 N 次、关闭的来源、从未成功、缺口刚好 6 小时）以及管理页面显示；`tests/core/test_schedule.py`、`tests/test_admin.py`、`tests/test_cli_schedule.py` 必须仍然通过。

# 不做
- 不做主动推送（邮件、聊天通知）。
- 不改运行记录的格式，不改开关和锁的行为。
