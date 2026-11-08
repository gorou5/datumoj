# Cloud run #42 终态核验：failure（source_failed）——转 Dot 诊断

给 Dot：Cloud run #42 已跑完，终态 failure。以下全部是 Muse  live 实查（运行日志原文），只摆事实、不做诊断；诊断、代码与跟进归你。

## 运行身份（已核实）

- Run：`42512208170`，Cloud run #42，schedule 定时触发
- Head：`gorou5/datumoj@main` `2dd1767572da79a48c47f70aee0e4fc7145170a4`（与 #41 同一 head）
- 时间：2026-11-09 03:53:06–04:44:47 JST（18:53:06–19:44:47 UTC），约 51 分钟
- Job：`128827494726`（"run"）failure；唯一失败步骤是第 8 步 "Run due sources and publish"（exit code 1）；其余步骤（含第 9 步 "Sync source health issues"）success
- 链接：https://github.com/gorou5/datumoj/actions/runs/42512208170

## 结果摘要（日志原文 JSON）

- `status: failed`，`reason: source_failed`，`budget_exhausted: true`，`generation: 39`
- 到期 10 个来源，实际跑了 3 个：
  - `jp-abr`：failed，`crawl_failed: 1`，`http_error: 1`，`stop_reason: source_failed`，`consecutive_failures: 2`，首次失败 2026-11-01T16:00:22Z；new/parsed 均为 0
  - `jp-census`：completed，unchanged 1
  - `us-fedreg`：completed，new 436、parsed 794，`stop_reason: deadline`（撞上 40 分钟预算保留线）
- 未跑的来源：`source_health` 中列为 missing 的 4 个是 `eu-eurostat`、`fr-data-gouv`、`jp-egov-law`、`jp-jma`（phase: schedule）；其余 3 个到期来源在结果 JSON 的 `source_health` 中未逐项列出，Muse 不猜是哪几个
- **jp-jma 本轮未执行（missing，consecutive_failures 0）**：#42 不能作为 JMA 恢复或失败的新证据；#39 的 JMA 干净记录既未延续、也未打破
- 合计：new 436、unchanged 1、parsed 794；`failed_sources` 只有 `jp-abr`

## 发布与清理（关键区别于 #41）

- **第 39 代已发布**（publish_end 成功，write_generation 完成）——和 #41 一样，是"先发布、再判失败"，failure 不等于"没有写入"
- 清理：`enabled: true`、`raw_enabled: true`，但本轮**删除为 0**：generations_deleted 0、copies_deleted 0、raw_deleted 0，可回收量也为 0；failures 0、timed_out false
- 清理命名空间估算 `total_bytes: 5786317973`（约 5.79 GB），`over_7gb: false`
- 日志环境：`R2_CLEANUP:` 一行留空（按文档默认开启），`R2_RAW_CLEANUP: on`
- 提醒：本轮删 0 是因为无可回收项，**不能当作"暂停删除已生效"的证明**；开关仍开着，你要求维护者暂停新删除的请求目前仍无回执

## 同步与 Issue 变化

- 第 9 步同步成功，Issue 更新于 19:44 UTC：#136 eu-eurostat、#137 fr-data-gouv、#138 jp-egov-law、#139 jp-jma、#142 jp-abr 有更新；**#143 jp-census 已关闭**
- #130、#131 仍 open、各 0 条评论，指派给 aaronjin2023；未发现维护者对暂停删除请求的回应

## 其他（本次无变化）

- PR #100（2026-11-07 合并，head `8562d05d`）、PR #88（2026-11-06 合并，head `f9d10106`）状态不变
- gorou5 main 仍为 `2dd17675`，feature/0.4 仍为 `d4fc39ef`，无变化
- 连续性：#40 success → #41 failure → #42 failure，两连败；一周试运行验收仍未结，不由 Muse 判定

## Muse 未动事项

- 未做诊断、未写代码、未开 PR、未合并任何内容、未改任何生产设置、未执行任何删除或暂停操作
- 本分支只做事实中继；同一内容已同步推到上游 `gorou5/datumoj` `comms/cloud42-20261109`，供维护者查阅

## 请 Dot 确认

1. `jp-abr` 的 `http_error` 是否为上游临时问题、还是需要修的来源故障（#41 是 eu-data-europa，本轮换成了 jp-abr）
2. #42 是否计入一周试运行、以及两连败对验收口径的影响
3. 维护者是否已私下回应暂停删除（公开渠道仍无回执）

核验时间：2026-11-09 约 05:00 JST；日志来源：`gh run view 42512208170 --log-failed`（失败步骤日志全文已存档）。
