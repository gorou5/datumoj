---
id: DATUMOJ-IVY-107
title: 抓取开关与调度：control.json、schedule tick/status/enable/disable、运行锁、launchd 安装
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/schedule.py", "src/datumoj/cli.py", "tests/core/test_schedule.py", "tests/test_cli_schedule.py", "results/DATUMOJ-IVY-107/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "sources/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_cli.py", "tests/core/test_runner.py", "tests/core/test_runner_file_cap.py"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_schedule.py
    - cmd: make check T=tests/test_cli_schedule.py
    - cmd: make check T=tests/test_cli.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-107/summary.md 完成说明"]
  review: {by: other_family, checklist: ["新机器默认是否全部关闭", "总开关或来源开关关闭时 tick 是否一定不抓", "同一来源是否不会并发运行（锁）", "进程崩溃留下的锁能否被识别并回收", "launchd 生成的 plist 是否正确且不会在测试里真的安装", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
每个抓取来源都有本机开关，另有一个总开关；一个调度入口按开关和各来源的 `schedule` 自动抓取，同一来源不会并发运行；可以一条命令生成或卸载 macOS 的 launchd 定时任务。

# 背景
开发线：A 抓取基础。人决定（2026-10-21）：JMA 全量每小时轮询，并且**所有抓取通道都要能开关**，测试完本地开发机器可以关闭抓取；之后由管理页面（DATUMOJ-IVY-610）调用本任务的接口。

现有：`datumoj crawl SOURCE` 调用 `core/runner.py` 的 `run_crawl`；`sources/*.yaml` 的 `schedule` 取值目前有 `hourly`、`daily`、`monthly`（契约里还允许 `manual` 或空）。计划见 `docs/PLAN.md`；测试全部离线，不能改依赖和契约。

# 要求
1. **开关状态**：存放在数据目录下的 `control.json`（数据目录同 CLI：`--data-dir` / `DATUMOJ_DATA_DIR` / `./data`，不进 git），内容为总开关 `enabled` 和每个来源的 `sources.<id>.enabled`。**文件不存在或来源未出现时一律视为关闭**（新机器默认全部关闭）。读写要原子（临时文件 + rename），坏文件报清楚的错误而不是静默当作开启。
2. **`core/schedule.py`** 提供可被 CLI 和管理页面复用的函数（签名自定，写进 summary）：读写开关、计算每个来源的“下次到期时间”（`hourly`=上次开始后 1 小时、`daily`=1 天、`monthly`=30 天、`manual`/空=从不自动运行）、`tick()`、`run_now(source_id)`、`status()`。
3. **`tick()`**：总开关和来源开关都开、并且已到期的来源才运行；逐个运行（不并发），每次调用现有的抓取（`run_crawl`）；运行结束后记录运行记录（开始、结束、新增、未变、失败按原因计数、停止原因、退出状态），供 `status()` 和页面读取，存在数据目录里（格式自定，例如 `runs.jsonl` 或 SQLite），只追加。
4. **运行锁**：每个来源一个锁（例如数据目录下的锁文件，配合 `fcntl.flock`），已有运行时 `tick()` 和 `run_now()` 都跳过并说明“正在运行”；进程崩溃后残留的锁能被识别（例如锁文件里记录 PID，进程不存在则回收）。
5. **`run_now(source_id)`**：不看到期时间，立即运行一次；但仍受总开关、来源开关和锁约束（开关关闭时拒绝并说明原因）。
6. **CLI**（在 `cli.py` 新增 `schedule` 子命令组，现有命令不变）：`datumoj schedule status`（每个来源：开关、调度、上次运行及结果、下次到期、是否正在运行）、`schedule enable|disable [SOURCE|--all]`、`schedule master on|off`、`schedule tick`、`schedule run SOURCE`、`schedule launchd show|install|uninstall`。
7. **launchd**：`show` 打印 plist（Label `com.datumoj.schedule`，每 5 分钟运行一次 `datumoj schedule tick`，工作目录为仓库根，标准输出和错误追加到数据目录下 `logs/schedule.log`，uv 路径按 Makefile 的规则查找）；`install` 写入 `~/Library/LaunchAgents/com.datumoj.schedule.plist` 并 `launchctl load`；`uninstall` 先 `unload` 再删除。安装路径和 `launchctl` 调用都要可注入，**测试里绝不能真的安装或调用 launchctl**。
8. 原有 `tests/test_cli.py`、`tests/core/test_runner.py`、`tests/core/test_runner_file_cap.py` 已列为 protected，必须全部通过。
9. 新测试 `tests/core/test_schedule.py` 和 `tests/test_cli_schedule.py`（时钟可注入，用 `datumoj.testing.fakes` 或回放，不联网）：默认全部关闭；开关组合（总开关关、来源关、都开）；到期计算；运行记录；锁阻止并发、残留锁回收；`run_now` 受开关约束；launchd 的 plist 内容正确且安装函数调用了注入的假 `launchctl`。

# 不做
- 不做管理页面（610）、不改 sources 配置、不改执行器和采集器、契约。
