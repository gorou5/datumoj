---
id: DATUMOJ-MAPLE-108
title: 运行账本（D-08）：run_id、运行中 / 结束 / 中断状态、坏尾行隔离、管理页显示启动失败
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-002"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/schedule.py", "src/datumoj/core/monitor.py", "src/datumoj/admin/web.py", "tests/core/test_run_ledger.py", "tests/core/test_schedule.py", "tests/regressions/test_audit_108.py", "results/DATUMOJ-MAPLE-108/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/core/test_monitor.py", "tests/test_admin.py"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_run_ledger.py
    - cmd: make check T=tests/core/test_schedule.py
    - cmd: make check T=tests/core/test_monitor.py
    - cmd: make check T=tests/test_admin.py
    - cmd: make check T=tests/regressions/test_audit_108.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-108/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
抓取运行被中断（进程被杀、机器重启）后能看出来；`runs.jsonl` 最后一行写坏时 `datumoj schedule status` 和管理页面仍能工作并给出告警；管理页启动运行失败时页面如实显示。

# 背景
开发线：A 云端运行与运行基础。审核 D-08：`Scheduler`（`core/schedule.py`）只在运行结束或普通异常后追加 `data/runs.jsonl`，开始时不写记录；`_history` 一次读全文件，任一行不是合法 JSON 就整体抛 `ValueError`；管理页（`admin/web.py`）后台线程启动失败的异常被吞掉，而页面已显示“已开始”。监控（`core/monitor.py`）读运行记录算提醒。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-108/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 每次运行生成 `run_id`；开始时追加一条 `state: running` 记录，结束时追加同 `run_id` 的 `state: final` 记录（含现有的全部字段）。已有的、不带 `run_id` / `state` 的旧记录按 final 读取，格式向后兼容。
2. 读取时：只有 running 没有 final 的 `run_id`，若对应来源的运行锁已不被持有（进程不在了），判为 `interrupted`；`status()` 每个来源显示最近一次运行的状态（含 `interrupted`）。
3. 坏行（不是合法 JSON 或缺必填字段）跳过并计数，`status()` 顶层给出 `ledger_warnings`（行号和原因，不含行内容全文）；不因坏行整体报错。追加新记录前若文件末尾不是换行，先补换行，避免把新记录接在坏尾行上。
4. 监控：`interrupted` 运行算作失败参与 `consecutive_failures`；running 的不算。
5. 管理页：后台运行启动失败（线程创建或第一步就抛错）时，页面显示失败和原因（转义），不显示“已开始”。
6. 新测试 `tests/core/test_run_ledger.py` 覆盖：开始和结束记录、中断判定（锁已释放 / 仍持有）、坏尾行和中间坏行、补换行、旧格式兼容、监控计数、管理页启动失败。`tests/core/test_schedule.py`、`tests/core/test_monitor.py`、`tests/test_admin.py` 必须仍然通过。审核回归测试 `tests/regressions/test_audit_108.py`：用合成数据离线复现审核场景——`runs.jsonl` 最后一行是半截 JSON 时，`Scheduler.status()` 仍能返回并给出告警——断言修好以后的行为，必须通过（不能标 skip / xfail）。全部已有测试（`make check T=tests`）必须仍然通过。
7. （2026-10-23 第 1 次尝试后补充）`tests/core/test_schedule.py` 的 `test_tick_due_and_run_now_bypasses_due_with_append_only_records` 断言 `runs.jsonl` 正好 4 行、首行与旧格式逐字相同，与“开始写 running、结束写 final”冲突。允许只调整这个测试里关于行数和行内容的断言（按 run_id 配对后，final 记录的原有字段与原断言一致；`last_run` 对应最近一次 final），不改测试的其他意图，在 summary 里说明；该文件其他测试不改。

# 不做
- 不改运行锁和定时的行为。
- 不做云端运行记录（MAPLE-601）。
