# CI #421 主线红的修复候选——Dot 交接中继（供维护者审核）

给维护者：这是 Dot 在 `zenithli/datumoj` 交接分支 `dot/admin-lock-probe-fix-20261109`（@ `62ab3f45`，2026-11-09 22:09:01 JST）提交的内容，Muse 原文逐字中继到本分支，未改动任何一个字节。以下全部是 Muse live 实查，只摆事实、不做诊断；诊断、代码与跟进归 Dot，是否合入由维护者审核后按正常流程决定，Muse 不合并、不开 PR、不触发工作流、不改生产设置。

## 交接内容（与 Dot 原件逐字一致）

- `comms/2026-11-09-from-dot-ci421-admin-lock/README.md`：交接说明全文
- `comms/2026-11-09-from-dot-ci421-admin-lock/admin-lock-probe.patch`：最小修复补丁（SHA256 `d531077d7b2dfa716752e5325cd0fd835389adb7d3eb5f4571748917964181ce`，与 `verification.json` 所载一致）
- `comms/2026-11-09-from-dot-ci421-admin-lock/cross_process_probe.py`：跨进程复核脚本
- `comms/2026-11-09-from-dot-ci421-admin-lock/verification.json`：验证记录

## Dot 交接要点（转述，不展开诊断）

- 目标基线：`gorou5/datumoj` main `b9f7d03`；针对 CI run 42618472890（#421）的唯一失败 `tests/test_admin.py::test_run_now_respects_switches_and_runs_in_background`（`started.wait(10)` 为 False；1 failed、5787 passed、1 xfailed）
- 候选根因：管理页状态探测短暂取得来源独占锁，后台 `Scheduler._run()` 非阻塞取锁时误判任务已在运行而返回 skipped，抓取未执行；管理页忽略 skipped 并标记 run-started 返回 303
- 复现与验证（Dot 自述）：原实现事件控制复现 10/10 次失败；修复后同一回归 100/100 次通过；相关套件 82 passed、Ruff 通过、跨进程探测通过；原 `tests/test_admin.py` 完全不变，`THREAD_WAIT=10` 保留
- 补丁范围：`src/datumoj/rikolto/core/schedule.py`（复用现有 `runs.lock` 作短时协调锁）＋新增 `tests/test_admin_lock_probe.py`
- Dot 明确的边界：只证明代码竞态存在且与 #421 症状一致，不能证明托管 CI 当次必然走了这条路径；82 项是部分快照、不是完整 5789 项回归；未在原 CI runner 或原负载下复现；未跑原生 Windows；新旧进程混用未验证；补丁不宣称修复全部启动确认与请求去重问题；不修改 #141 的读取工作流、不改容量/删除策略；交接本身不授权合并、触发工作流或生产操作；main 保持不变

## Muse 的独立核对（本轮实查）

- 补丁 SHA256 与 `verification.json` 所载逐字一致：`d531077d7b2dfa716752e5325cd0fd835389adb7d3eb5f4571748917964181ce`
- `verification.json` 所列 5 个原始 blob hash 与本仓 main `b9f7d03` 逐字一致（schedule.py / admin/web.py / test_admin.py / uv.lock / pyproject.toml）
- 补丁在 main `b9f7d03` 的干净检出上 `git apply --check` 通过（仅检查，未应用、未提交到 main）
- CI #421 现状与 Dot 所述一致：run 42618472890，push，head `b9f7d03`，conclusion failure，仍是 attempt 1；其后 5 个 CI run 均为 tel 分支的 PR run（全部 success），不是主线重跑；main 仍为 `b9f7d03`
- PR #100（2026-11-07 合并，head `8562d05d`）、PR #88（2026-11-06 合并，head `f9d10106`）状态不变；feature/0.4 仍为 `40403c8`
- Cloud run 最新仍是 #47（run 42598641403，2026-11-09 19:08 JST 开始，success）；#48 尚未开始

## 未动事项

- 未应用补丁到任何分支、未开 PR、未合并、未直推 main/feature/0.4、未改仓库设置、未删除任何内容、未触发任何工作流
- 一周试运行验收仍未结，不由 Muse 判定；补丁通过不等于验收完成

核验与中继时间：2026-11-09 约 22:18 JST。
