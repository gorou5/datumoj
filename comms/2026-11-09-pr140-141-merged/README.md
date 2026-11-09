# PR #140 / #141 已合并进 main，主线 CI 现为红——转维护者查阅

给维护者：gorou5/datumoj 的 main 刚推进了，PR #140 与 #141 已合并。以下全部是 Muse live 实查的事实记录，只摆事实、不做诊断；诊断归 Dot、代码与跟进归维护者。

## 变化（已核实）

- gorou5 main：`ac47873` → `b9f7d03`（2026-11-09 21:36:47 JST）
- PR #140「任务副本：MAPLE-904、903、148、149」：closed、merged，合并者 `sandal9u`，合并时间 2026-11-09 21:25:24 JST；head `7f928bc`；合并提交 `86a08e0`；该提交的 CI（run 42616889981）success
- PR #141「检查点同步：只读的读取代次工作流（MAPLE-904）与 MAPLE m0–m2 验收记录」：closed、merged，合并者 `aaronjin2023`，合并时间 2026-11-09 21:36:47 JST；head `40403c8`；合并提交 `b9f7d03`
- 两份 PR 均无人类 APPROVED 评审记录：#141 只有 COMMENTED（CodeRabbit bot＋aaronjin2023 评论）；#140 只有 CodeRabbit bot 的 COMMENTED
- 合并时分支 CI 是绿的：#141 head `40403c8` 的 CI（run 42544910339，2026-11-09 10:18:50 JST）success；#140 head `7f928bc` 的 CI（run 42611243595，20:43:11 JST）success。此前 #141 曾红过的时钟 flake 测试，在合并前已由一次通过的重跑取代

## 合并内容（compare ac47873...b9f7d03，9 个提交、14 个文件、只增不改旧逻辑为主）

- 新增 `.github/workflows/cloud-pull-check.yml`（只读的读取代次工作流）、`scripts/pull_check.py`
- 新增 `docs/acceptance/v0.3.0-maple-m0.md`、`m1.md`、`m2.md`，`docs/tasks/DATUMOJ-MAPLE-148.md`、`149.md`、`903.md`、`904.md`
- 新增测试 `tests/test_pull_check.py`、`tests/test_workflows_pull_check.py`、`tests/regressions/test_audit_148.py`，`tests/analysis/test_tools.py` 有改动
- 唯一源码改动 `src/datumoj/rikolto/analysis/tools.py`（+12/-2）：按 locator 查快照时，除 chunks/observations 外也取 datasets，`kind: catalog_entry` 形式加入 passages；observations 的 passage 补了 `text`（JSON dump）

## 主线 CI 现为红（合并提交上的 CI 失败）

- Run 42618472890（push，head `b9f7d03`，2026-11-09 21:36:50–21:49:03 JST）：validate success，test failure
- 结果：1 failed、5787 passed、1 xfailed；唯一失败 `tests/test_admin.py::test_run_now_respects_switches_and_runs_in_background`——后台线程 10 秒内未启动（`started.wait(10)` 为 False），日志原文见 `~/workspace/datumoj-check/wake-20261109-2148/run141merge-logs.zip`（test/6_Run tests.txt）
- 这不是 #141 曾红过的时钟 flake 测试（那一项本轮通过了）；Muse 不判断是偶发超时还是真回归

## 其他（本次无变化）

- PR #100（2026-11-07 合并，head `8562d05d`）、PR #88（2026-11-06 合并，head `f9d10106`）状态不变
- feature/0.4 仍为 `40403c8`；zenithli/datumoj main 仍为 `4e0f895`，无 Dot 新回信、无新 handoff
- 事件里带的 run#42482781119 是 Cloud run #40（2026-11-08 21:59–22:46 JST success，head `0b09687c`），00:03 已核验过，是指纹重复检出、不是新事件
- Cloud run #48 尚未开始；下一轮定时运行将在新 head `b9f7d03` 上起跑
- 连续性：#40 S → #41 F → #42 F → #43 S → #44 S → #45 S → #46 S → #47 S（五连 success）；一周试运行验收仍未结，不由 Muse 判定——合并不是验收完成

## Muse 未动事项

- 未做诊断、未写代码、未开 PR、未合并任何内容、未改任何生产设置、未执行任何删除或暂停操作
- 本分支只做事实中继；同一内容已同步推到 `zenithli/datumoj` `comms/muse-to-dot-20261109-pr140-141-merged`，供 Dot 查阅

## 请维护者知悉

1. 合并已记录：#140（sandal9u，21:25 JST）、#141（aaronjin2023，21:36 JST），均无人类 APPROVED 评审记录
2. 合并提交 `b9f7d03` 的 CI（run 42618472890）失败，test 作业 1 项失败（`test_admin.py` 线程 10 秒超时），main 现为 CI 红——请知悉
3. 下一轮 Cloud run 将在新 head `b9f7d03` 上跑
4. #139/#142 仍 open；#115 暂停删除请求仍无回执（最新评论仍是 2026-11-08 11:32 UTC）

核验时间：2026-11-09 约 21:48 JST。
