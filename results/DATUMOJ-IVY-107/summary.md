# DATUMOJ-IVY-107 完成说明

已实现本机抓取开关、调度核心、schedule CLI、来源运行锁及 launchd 生成/安装/卸载。只修改任务 scope 内文件，未提交 git commit，未更改依赖、契约、来源配置或受保护测试。

## 可复用接口

均位于 `src/datumoj/core/schedule.py`，`data_dir` 接受 `str | Path`：

```python
read_control(data_dir) -> dict
write_control(data_dir, control: dict) -> None
set_enabled(data_dir, enabled: bool, source_ids: Iterable[str] | None = None) -> dict
next_due(schedule: str | None, last_start: datetime | None, now: datetime) -> datetime | None

Scheduler(data_dir, sources: Iterable[SourceSpec], execute: Callable[[SourceSpec], Any],
          *, now: Callable[[], datetime] = utcnow)
Scheduler.tick() -> list[dict]
Scheduler.run_now(source_id: str) -> dict
Scheduler.status() -> dict

launchd_plist(repo_root, data_dir, *, uv: str | None = None) -> bytes
install_launchd(repo_root, data_dir, *, install_path: Path | None = None,
               launchctl: Callable | None = None, uv: str | None = None) -> Path
uninstall_launchd(*, install_path: Path | None = None,
                 launchctl: Callable | None = None) -> Path
```

`source_ids=None` 表示更新总开关；传入来源 ID 列表只更新这些来源。增量更新在 `control.lock` 下完成整个读改写过程，防止并发更新丢失；完整替换使用 `write_control`。控制文件使用同目录临时文件、flush/fsync、`os.replace`，读者只能看到完整版本。缺失的文件、字段或来源开关均为关闭；无效 JSON、非布尔开关等明确报错。内容示例：

```json
{"enabled": false, "sources": {"jp-jma": {"enabled": false}}}
```

调度核心仅依赖契约与标准库。`execute(spec)` 应返回与现有 `run_crawl` 报告一致的 `new`、`unchanged`、`failures`、`stop_reason` 字段；CLI 注入现有 wiring 的 `run_crawl`，复用原有采集器初始化、fetcher 关闭与异常处理。页面可以注入同样的执行函数；测试使用假实现。`now()` 应返回带时区的 datetime，默认 UTC。

`next_due` 按上次开始计算：hourly +1 小时、daily +1 天、monthly +30 天。没有上次记录的自动来源立即到期；manual/空返回 None。失败运行也从其开始时间计算下次到期。未知调度值明确报错。

## 运行与记录

`tick` 按来源顺序逐个执行；每次获得来源锁后重新检查总开关、来源开关和最新运行记录。`run_now` 忽略到期时间，仍检查两级开关和锁。跳过结果含 `status=skipped` 与中文 `reason`，包括“总开关关闭”“来源开关关闭”“尚未到期”“不自动运行”“正在运行”。跳过不会追加一次虚假的运行记录。

每次执行完成后向数据目录 `runs.jsonl` 追加一行 JSON，以 `runs.lock` 串行写入并 flush/fsync。字段包括：`source_id`、`started_at`、`ended_at`、`new`、`unchanged`、`failures`（原因到次数的映射）、`stop_reason`、`exit_status`（0/1）、`status=completed`。采集器/存储等异常按异常类型记一次失败并保留异常说明；没有返回报告时新增、未变数量为 null，避免把未知数补为零。执行失败会记录并继续后续来源；记录本身写入失败则抛错，不能冒充成功。

`status()` 返回总开关及每个来源的开关、实际生效开关、schedule、last_run、next_due、running。next_due 表示调度计算出的到期点；实际能否执行还取决于开关与锁。

来源锁在 `locks/<source_id>.lock`，使用非阻塞 `fcntl.flock`；成功开始时记录 PID 和开始时间。锁文件不删除，所有进程始终锁同一 inode。内核在进程退出/崩溃时释放 flock，后继运行可取得残留文件的锁并覆写 PID，无需按 PID 猜测或删除活锁。测试用独立子进程持锁并 `os._exit`，验证运行阻止、崩溃恢复和 inode 保持不变。

## CLI 与 launchd

新增命令：

```text
datumoj [--data-dir DIR] schedule [--data-dir DIR] status
datumoj schedule enable|disable [SOURCE|--all]
datumoj schedule master on|off
datumoj schedule tick
datumoj schedule run SOURCE
datumoj schedule launchd show|install|uninstall
```

目录优先级：schedule 组的 `--data-dir`、全局 `--data-dir`、`DATUMOJ_DATA_DIR`、`./data`。所有输出除 plist 外均为 JSON。enable/disable 只改来源开关，总开关由 master 单独设置；参数必须恰好指定 SOURCE 或 --all。tick 正常跳过返回 0，抓取失败返回 1；run 被开关/锁拒绝或抓取失败返回 1；参数/控制内容错误返回 2。原有命令行为保留。

plist Label 为 `com.datumoj.schedule`，StartInterval 为 300 秒，工作目录为仓库根目录，绝对参数数组执行 `uv run --frozen datumoj --data-dir <绝对数据目录> schedule tick`。stdout/stderr 均指向 `<数据目录>/logs/schedule.log`，由 launchd 追加写入。uv 查找顺序为显式注入、环境变量 UV、PATH 中 uv、`/opt/homebrew/bin/uv`；保留 Homebrew 稳定符号链接路径。

install 原子写入默认 `~/Library/LaunchAgents/com.datumoj.schedule.plist` 后调用 load；uninstall 先 unload，成功后才删除，失败保留文件。路径与调用器均可注入；假调用器接收完整参数数组和 `check=True`。show 不安装、不执行 launchctl。所有新测试均拦截真实 subprocess.run，安装相关测试只写临时路径并调用假 launchctl。

## 验收与逐项核对

| 要求 | 处理及证据 |
| --- | --- |
| 1 开关状态 | 默认关闭、布尔校验、原子替换、替换失败保留旧文件测试通过。 |
| 2 复用接口与到期 | 上述接口；时钟注入，小时/天/30 天/manual/空与未知值测试通过。 |
| 3 tick 与追加记录 | 串行、重查开关、失败分类、结束时间、追加保留旧记录测试通过。 |
| 4 来源运行锁 | 非阻塞 flock、运行中跳过、独立进程崩溃释放、残留锁复用测试通过。 |
| 5 run_now | 忽略到期/manual，仍受两级开关、锁控制；未知来源拒绝。 |
| 6 CLI | 所有要求的 schedule 子命令及目录优先级测试通过；原 CLI 回归通过。 |
| 7 launchd | plist 完整字段、uv 查找、临时路径安装/卸载和注入假调用器测试通过。 |
| 8 受保护回归 | CLI、runner、runner_file_cap 均通过且文件未修改。 |
| 9 离线新测试 | 新增核心 24 项、CLI 14 项；不联网，无真实安装或 launchctl 调用。 |

任务未提供“评审中的高置信度质疑”条目。审查清单六项均已按上述对应证据自查；跨模型审查由调度器后续执行，本次没有另行启动 agent。

最终实际执行：

| 命令 | 结果 | 日志 |
| --- | --- | --- |
| `make check T=tests/core/test_schedule.py` | 24 passed | `core-schedule.log` |
| `make check T=tests/test_cli_schedule.py` | 14 passed | `cli-schedule.log` |
| `make check T=tests/test_cli.py` | 4 passed | `cli-regression.log` |
| `make check T=tests/core/test_runner.py` | 46 passed | `runner-regression.log` |
| `make check T=tests/core/test_runner_file_cap.py` | 3 passed | `runner-file-cap-regression.log` |

总计 91 项通过，五条命令退出码均为 0。make 按仓库已有规则自动回退到主仓库只读 .venv；未安装或修改依赖。

附加对四个变更 Python 文件运行 ruff，仅报告 CLI 原有 search/eval 两处 B008；原有代码位置已通过 git HEAD 核对，见 `lint.log`、`lint-baseline.log`。新逻辑没有额外 lint 问题。变更文件清单见 `changed-files.txt`。

## 跳过、未核对与风险

未做真实抓取、真实 launchd 安装/卸载或全仓测试；未写入开发机实际 control.json，现有抓取开关未被开启。原 `datumoj crawl` 按任务“现有命令不变”保留直接手动执行行为，开关和调度锁约束 schedule tick/run。

关闭开关阻止后续启动，不中断已经运行的采集器。进程被强杀时未完成的执行可能没有结束记录；JSONL 追加若遭遇中途崩溃产生残缺行，会明确报错、保留原文件，需人工检查后修复，不自动丢弃历史。
