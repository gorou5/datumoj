# Cloud run #49 终态核验：failure（第 46 代已发布，eu-data-europa 三连失败、JMA 420 件、清理又删约 305MB、命名空间距 7GB 线仅约 46MB）

给维护者：Cloud run #49 已跑完，终态 failure。以下全部是 Muse live 实查（运行日志原文），只摆事实、不做诊断；诊断、代码与跟进归 Dot，合并与设置改动等 zenith 拍板。

## 运行身份（已核实）

- Run：`42652202730`，Cloud run #49，schedule 定时触发
- Head：`gorou5/datumoj@main` `b9f7d0397501ba318bc3a06ec648199c1606e195`（与 #48 同一 head）
- 时间：2026-11-10 01:11:20–01:59:15 JST（2026-11-09 16:11:20–16:59:15 UTC），约 48 分钟
- Job：`129281226859`（"run"）completed／failure；13 个步骤中仅第 8 步 "Run due sources and publish" failure（exit 1），第 9 步 "Sync source health issues" success
- 链接：https://github.com/gorou5/datumoj/actions/runs/42652202730

## 结果摘要（日志原文 JSON）

- `status: failed`，`exit_status: 1`，`reason: source_failed`，`budget_exhausted: true`，`generation: 46`，`reserve_minutes: 25.0`，`weekly_report_key: null`
- 到期 7 个来源，实际跑了 3 个：
  - `eu-data-europa`：**failed**，new 0、parsed 0，`failures: {"crawl_failed": 1}`，`stop_reason: source_failed`；source_health `failed`／crawl、`{"network_error": 1}`，**`consecutive_failures: 3`，`first_failure_at: 2026-11-06T07:10:17Z`**
  - `jp-jma`：completed，new 420、parsed 420，`failures: {}`，`stop_reason: deadline`，`consecutive_failures: 0`；source_health `deadline`／crawl
  - `jp-bodik`：degraded，new 4、unchanged 87、parsed 90，`failures: {"crawl_failed": 1}`，`stop_reason: deadline`，`consecutive_failures: 0`；source_health `degraded`／crawl、`{"http_error": 1}`
- `failed_sources` 只有 `eu-data-europa` 一项；其余 4 个到期来源在结果 JSON 中未逐项列出，不猜测
- **jp-jma 在已核验的六轮中全部 0 失败**：#43（424/424）→ #45（700/700）→ #46（469/469）→ #47（419/419）→ #48（520/520）→ #49（420/420）
- 合计：new 424、unchanged 87、parsed 510；`vector_pending: 16321`（与 #48 相同）

## 发布与清理

- **第 46 代已发布**（publish_start／publish_end 成功）——failure 不等于无写入
- 清理：`enabled: true`、`raw_enabled: true`，本轮**又实际删除**：generations_deleted 2、copies_deleted 598（155,068,106 B，约 148 MB）、raw_deleted 445（150,314,554 B，约 143 MB），`raw_marked: 420`；合计删 305,382,660 B（约 305 MB）；failures 0、timed_out false
- 清理命名空间估算 `total_bytes: 6953693958`（约 6.95 GB），`over_7gb: false`（#48 时 6,887,577,580 B，本轮净增 66,116,378 B，约 66 MB）
- **只摆事实**：距 7,000,000,000 B 的线仅剩 46,306,042 B（约 46 MB）；#48 轮时还剩约 112 MB
- 提醒：Dot 要求“暂停所有新的清理删除”的请求仍无回执（#115 最新评论仍是 2026-11-08 11:32 UTC 的恢复定时说明；#130/#131 仍 open、各 0 条评论，本轮实查）

## 同步与 Issue 变化

- 第 9 步同步步骤的日志中无 Issue 更新输出；各 Issue 状态与评论数均未变：
  - #139 jp-jma 仍 open（最后更新仍是 #42 同步的 2026-11-08 19:44:45 UTC）——JMA 已六轮 0 失败，同步步骤仍未关闭它
  - #142 jp-abr 仍 open（同上，19:44:40 UTC）
  - #136/#137/#138 已关闭（#43 同步）；#143 已关闭（#42 时）
- #115、#130、#131 仍 open，无维护者对暂停删除请求的回应

## 其他（本次无变化）

- PR #100（2026-11-07 合并，head `8562d05d`）、PR #88（2026-11-06 合并，head `f9d10106`）状态不变
- gorou5 main 仍为 `b9f7d03`，feature/0.4 仍为 `40403c8`；main CI 仍红，仍是同一失败 run `42618472890`（CI #421），其后无主线重跑；Dot 的修复候选（admin-lock-probe）已中继，待审核
- 连续性：#40 success → #41 failure → #42 failure → #43 success → #44 success → #45 success → #46 success → #47 success → #48 success → **#49 failure（六连 success 终结）**；一周试运行验收仍未结，不由 Muse 判定

## Muse 未动事项

- 未做诊断、未写代码、未开 PR、未合并任何内容、未改任何生产设置、未执行任何删除或暂停操作
- 本分支只做事实中继；同一内容已同步推到 `zenithli/datumoj` `comms/muse-to-dot-20261110-cloud49`，供 Dot 查阅
