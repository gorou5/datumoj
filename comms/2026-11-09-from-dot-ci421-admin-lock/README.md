# CI #421：管理页状态探测与后台运行锁竞态修复候选

给 Muse 与维护者：本交接提供可应用的最小补丁和离线验证。目标基线为 `gorou5/datumoj` main `b9f7d0397501ba318bc3a06ec648199c1606e195`。没有合并、部署、触发 CI、改生产配置或访问真实数据。

## 已证明的问题与证据边界

[CI #421](https://github.com/gorou5/datumoj/actions/runs/42618472890/job/129164192522)唯一失败是 `tests/test_admin.py::test_run_now_respects_switches_and_runs_in_background`：POST返回303后，`started.wait(10)` 为False。结果为 **1 failed、5787 passed、1 xfailed**。这不是先前的周报跨周测试失败。

离线用事件控制真实文件锁的交错，稳定复现一个能够产生相同症状的代码缺陷：

1. 管理页启动后台任务后调用 `Scheduler.status()`；状态探测会短暂取得来源的独占锁。
2. 此时后台 `Scheduler._run()` 对同一来源锁做非阻塞获取，看见探测占锁，就误判真实任务已经运行，返回 `skipped / 正在运行`。
3. 抓取函数没有执行；管理页忽略返回的 skipped 结果，并将已结束的线程标记为 `run-started`，返回303。

**原实现受控重复10/10次都得到“抓取调用为空、skipped正在运行、303 run-started”。** 测试没有伪造 OS 锁获取结果，也没有通过增加超时、sleep、跳过断言或改业务结果制造通过。

这证明真实代码竞态存在，并与 #421 的症状一致；**不能证明托管 CI 当次必然走了这条路径**，因为原日志没有后台返回值或当时锁/台账快照。没有将这次失败简单归为环境偶发，也没有断言它是 #141 新增逻辑导致的回归。

## 最小修复

`admin-lock-probe.patch` 只改两处范围：

- `src/datumoj/rikolto/core/schedule.py`：复用现有 `runs.lock` 作短时协调锁。状态探测从取得到释放来源锁全程持有协调锁；运行方仅在取得来源锁的短阶段持有协调锁，随后释放协调锁，但继续持有真实来源锁。
- 新增 `tests/test_admin_lock_probe.py`：以事件精确控制上述竞争，要求实际抓取恰好执行一次并完成。

协调锁**不跨越抓取执行**，也不使用抓取全程持有的 `workspace_lock` 包住状态查询。因此状态查询不会等待整个抓取完成。来源锁的独占、非阻塞竞争、崩溃后释放以及原锁文件身份保持不变；没有改成共享运行锁或阻塞等待来源锁。

状态探测只非阻塞尝试来源锁，避免与“持有来源锁再写运行台账”的路径形成循环等待。独立审查未发现此补丁的阻塞性正确性问题，并做了实际独立进程的探测/运行协调验证。

**原 `tests/test_admin.py` 完全不变，`THREAD_WAIT=10` 和原断言均保留。** 旧 [PR #108](https://github.com/gorou5/datumoj/pull/108) 已提高等待时限，本次没有重复该改法。

## 验证结果

- 原实现：事件控制复现10/10次失败，含安全的合成 skipped 结果。
- 修复后：同一回归重复100/100次通过。
- 本地可用的管理页、调度、来源到期、开关与CLI相关套件：**82 passed，1条原有fork警告**；独立复跑82项通过，耗时2.16秒。
- Ruff：`src tests` 检查通过。
- 真实跨进程验证：状态观察进程持有探测锁时运行方等待协调锁，探测释放后运行恰好完成一次。附 `cross_process_probe.py` 供复核。
- 原始调度代码、管理页代码、测试与依赖锁定文件按目标基线核对；详见 `verification.json`。

本地是经过文件身份核对的**部分仓库快照**，82项不是完整的5789项仓库回归。没有在原CI runner或原负载下复现；未运行原生Windows。补丁沿用现有POSIX/Windows锁实现，但不能据此声称Windows已经实测通过。全仓CI须在维护者审核、集成后的确切提交上另行验证。

新旧进程混用未验证；旧状态观察进程不参与新增协调，不能声称新运行方因此受到完整保护。应用时需要维护者按正常升级流程处理相关进程，而不是只替换文件后假定所有现存进程均已更新。

## 使用方式

在完整目标基线检出目录、安装锁定依赖后：

```sh
git apply --check /path/to/handoff/admin-lock-probe.patch
git apply /path/to/handoff/admin-lock-probe.patch
uv sync --frozen
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_admin_lock_probe.py tests/test_admin.py tests/test_admin_alerts.py tests/test_admin_eltiro.py tests/test_cli_schedule.py tests/core/test_schedule.py tests/core/test_schedule_due.py tests/core/test_schedule_enablement.py
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python /path/to/handoff/cross_process_probe.py
```

审核后按现有维护者流程集成并运行完整CI；此说明本身不授权合并、触发工作流或生产操作。不要因相邻分支绿灯就认为主线已恢复：检查时 #421仍是失败的attempt1，main仍是上述基线；#422–425属于tel分支，不是主线重跑。

## 有意保留的范围

此补丁修复**状态观察者导致运行被误跳过**，不宣称修复全部启动确认与请求去重问题。原管理页还会忽略其他 skipped 结果，例如预检查后来源开关被关闭、真正的竞争任务先获锁或远端租约未取得；并发手动请求也可能在工作区锁后依次执行。这些既有行为需单独确定范围，不能由本补丁的绿灯推定已解决。

没有修改 #141 的读取工作流、引用逻辑、来源告警、容量/删除策略，也没有将PR合并或本地测试通过等同于一周验收完成。交接仅含公开代码、合成测试和公开CI事实，不含生产台账、凭据或受限来源正文。
