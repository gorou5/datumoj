---
id: DATUMOJ-MAPLE-601
title: 监控和报告上云：运行记录与提醒随代次保存；按“预期频率内没有成功运行”提醒云端缺口；周报写到 R2
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-102", "DATUMOJ-MAPLE-105", "DATUMOJ-MAPLE-108"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/monitor.py", "src/datumoj/cloud.py", "src/datumoj/analysis/reports.py", "src/datumoj/cli.py", "src/datumoj/admin/web.py", "tests/core/test_monitor_cloud.py", "tests/test_cloud_reports.py", "results/DATUMOJ-MAPLE-601/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_monitor_cloud.py
    - cmd: make check T=tests/test_cloud_reports.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-601/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
在本机拉下来的副本上，`datumoj schedule status` 和管理页能看到云端各来源的运行情况和提醒；Actions 没按时跑（延迟、被跳过、失败）也会成为提醒；每周变化报告写到 R2，人随时能看。

# 背景
开发线：F 监控与遗留。计划 6.5 节。108 已有运行账本（`run_id`、running / final / interrupted），`core/monitor.py` 的 `evaluate_alerts` 已按本机历史出提醒；102 的代次已包含 `runs.jsonl` 和 `control.json`，`StateGeneration.last_successful_runs` 记各来源最后成功运行。Actions 运行本身失败时无法写状态，所以云端缺口只能从“最后一个有效代次”推断。周报已有 `analysis/reports.py` 的 `weekly_report`。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-601/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 云端缺口提醒：读当前数据目录记录的代次（106 写的拉取记录）和其中的 `last_successful_runs`，对每个已开启来源按其 `schedule` 判断“预期频率内没有成功运行”；代次本身的生成时间超过预期（例如 2 小时没有新代次）单独提醒“云端运行停滞”。JMA 等短保留来源的缺口沿用 602 的显式标记单独提醒。没有代次记录（本机自建数据）时行为不变。
2. `schedule status` 和管理页显示：当前代次号和生成时间、写入者（actions / local）、各来源最后成功运行，以及上面的提醒。
3. 周报上云：`datumoj cloud run` 在每周第一次成功发布后生成上一周的周报，写到 R2 的 `reports/weekly/<年-周>.md`（不可变，已存在就不覆盖），并把对象键记进运行摘要；`datumoj report weekly --from-state` 可在本机拉下来的副本上重新生成同一份。
4. 测试：按时运行时无提醒；某来源超过一个周期没成功时提醒；代次停滞时提醒；周报只在每周第一次写、不覆盖；本机自建数据时原有提醒测试全部不改且通过。
5. 测试断言：命令输出和日志里不出现测试用的凭证值、endpoint 和 bucket 名。
6. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不做主动推送（邮件、聊天）。
- 不改 Actions 工作流文件。
