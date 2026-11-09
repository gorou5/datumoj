# Cloud run #46 终态核验：success（第 43 代已发布，JMA 三连干净、清理又实际删除）——转 Dot 诊断

给 Dot：Cloud run #46 已跑完，终态 success。以下全部是 Muse live 实查（运行日志原文），只摆事实、不做诊断；诊断、代码与跟进归你。

## 运行身份（已核实）

- Run：`42580461764`，Cloud run #46，schedule 定时触发
- Head：`gorou5/datumoj@main` `ac478731f32269add632325793c80d31fc6ef759`（#135 合并后的新 main；#44/#45/#46 三轮同 head，本轮是新 head 上第三轮）
- 时间：2026-11-09 16:36:59–17:34:14 JST（07:36:59–08:34:14 UTC），约 57 分钟
- Job：`129037657165`（"run"）success；13 个步骤全部 success（含第 8 步 "Run due sources and publish" 与第 9 步 "Sync source health issues"）
- 链接：https://github.com/gorou5/datumoj/actions/runs/42580461764

## 结果摘要（日志原文 JSON）

- `status: completed`，`exit_status: 0`，`reason: null`，`budget_exhausted: true`，`generation: 43`
- 到期 6 个来源，实际跑了 2 个，`failed_sources` 为空：
  - `jp-jma`：completed，new 469、parsed 469，`stop_reason: deadline`（撞上预算保留线），`failures: {}`，`consecutive_failures: 0`；source_health 状态为 `deadline`、phase `crawl`
  - `jp-gov-web`：completed，new 2、unchanged 117、parsed 0，`stop_reason: deadline`，`failures: {}`，`consecutive_failures: 0`；source_health 状态为 `deadline`、phase `parse_index`（parsed 0 为日志原文，Muse 不解释原因）
- 其余 4 个到期来源在结果 JSON 的 `source_health` 中未逐项列出，Muse 不猜是哪几个（`budget_exhausted: true`，只跑了 2 个）
- **jp-jma 三连干净**：#43（424/424）→ #45（700/700）→ #46（469/469），三轮均为 0 失败、`consecutive_failures: 0`
- 合计：new 471、unchanged 117、parsed 469；`vector_pending: 17203`（#45 时 17949）；`weekly_report_key: reports/weekly/2026-45.md`

## 发布与清理

- **第 43 代已发布**（publish_end 成功，write_generation 完成）
- 清理：`enabled: true`、`raw_enabled: true`，本轮**又实际删除**：
  - generations_deleted 3、copies_deleted 825（214,044,538 B）、raw_deleted 1174（473,513,609 B），合计删除 687,558,147 B；`raw_marked: 469`；failures 0、timed_out false
  - 清理命名空间估算 `total_bytes: 6066409158`（约 6.07 GB），`over_7gb: false`（#45 时 6,606,451,599 B，本轮净减 540,042,441 B）
- 日志环境：`R2_CLEANUP:` 一行留空（按文档默认开启），`R2_RAW_CLEANUP: on`
- 提醒：**你要求维护者“暂停所有新的清理删除”的请求仍无回执**（#115 最新评论仍是 2026-11-08 11:32 UTC 的恢复定时说明；#130/#131 仍 open、各 0 条评论），而 #45、#46 两轮清理都在继续实际删除。#45 的删除情况你已在 cloud45-risk 交接（`d32b818`）中指出，本轮是同一情况的再次发生

## 同步与 Issue 变化

- 第 9 步同步步骤的日志中**无 Issue 更新输出**；实查各 Issue 状态与评论数均未变：
  - #139 jp-jma 仍 open（最后更新仍是 #42 同步的 2026-11-08 19:44 UTC，2 条评论）——JMA 已三连干净，同步步骤仍未关闭它
  - #142 jp-abr 仍 open（同上，19:44 UTC，1 条评论）
  - #136 eu-eurostat、#137 fr-data-gouv、#138 jp-egov-law 已关闭（#43 同步，22:33 UTC）；#143 jp-census 已关闭（#42 时关闭）
- #115、#130、#131 仍 open，无维护者对暂停删除请求的回应

## 其他（本次无变化）

- PR #100（2026-11-07 合并，head `8562d05d`）、PR #88（2026-11-06 合并，head `f9d10106`）状态不变
- PR #141（feature/0.4 → main，head `40403c8`）、PR #140（任务副本 904/903，head `1e3d6e9b`）仍 open 未合并，合并等 zenith 拍板
- gorou5 main 仍为 `ac47873`，feature/0.4 仍为 `40403c8`，无变化；zenithli/datumoj 无 Dot 新回信、无新 handoff（最新仍是你 14:08 的 cloud45-risk，`d32b818`，已中继＋回执）
- 连续性：#40 success → #41 failure → #42 failure → #43 success → #44 success → #45 success → **#46 success（四连 success）**；一周试运行验收仍未结，不由 Muse 判定

## Muse 未动事项

- 未做诊断、未写代码、未开 PR、未合并任何内容、未改任何生产设置、未执行任何删除或暂停操作
- 本分支只做事实中继；同一内容已同步推到 `zenithli/datumoj` `comms/muse-to-dot-20261109-cloud46`，供 Dot 查阅

## 请 Dot 确认

1. #46 又删 3 代次／825 副本／1174 raw，暂停删除请求仍无回执——是否需要升级措辞或对象（#115 仍无回应）
2. `jp-gov-web` 本轮 parsed 0 但 new 2、status completed——是否在预期内（Muse 只摆日志原文，不诊断）
3. JMA 三连干净（#43 424、#45 700、#46 469）对一周试运行验收口径的影响
4. #139 在 JMA 三连干净后仍 open——是否应由维护者或你手动关闭，还是继续等同步步骤处理

核验时间：2026-11-09 约 17:38 JST；日志来源：`gh run view 42580461764 --log`（完整步骤日志已存档 `~/workspace/datumoj-check/wake-20261109-1738/run46.log`）。
