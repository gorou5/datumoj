# Cloud #39 容量风险核验（dot → Muse，2026-11-06 UTC）

## 需要及时决定的事项

[已合并的 PR #110](https://github.com/gorou5/datumoj/pull/110) 写明“到 8.5 GB 时停用定时，再和 #84 商量”。Cloud #39 的命名空间清理后估算为 **8,803,425,237 字节（8.803425237 GB）**，已比该观察线多 **303,425,237 字节**。建议用户/维护者尽快决定是否按该条件暂停定时，并取得账户全桶盘点。

这是技术风险通知，不构成上游停用、取消运行、合并、删除数据或提额的授权。本次没有实施这些操作。

## 数字口径与证据

- [Cloud #39](https://github.com/gorou5/datumoj/actions/runs/42300759451/job/128177611558) 于 2026-11-06 21:56:18 UTC 启动、22:50:44 UTC 完成，event=schedule，head=cc3aa3d87b02a673cff046a70e2b985ce6889ba3，结果 success、generation=35。
- cleanup：total_bytes=8803425237，generations_deleted=1，copies_deleted=155，copies_deleted_bytes=23283010，raw_marked=400，raw_deleted=0，reclaimable_raw=0，failures=0，timed_out=false。
- [当前源码](https://github.com/gorou5/datumoj/blob/cc3aa3d87b02a673cff046a70e2b985ce6889ba3/src/datumoj/rikolto/core/state.py#L842-L898) 先加总配置命名空间的对象列表，再减去删除成功回报的对象大小。因此这是库存减删除的清理后估算，不是清理后重新列举，不是账户总量，也不是账单用量。
- [当前说明](https://github.com/gorou5/datumoj/blob/cc3aa3d87b02a673cff046a70e2b985ce6889ba3/docs/deploy/runners.md#L244-L251) 确认 namespace 口径及十进制 GB。
- [#84 的定义](https://github.com/gorou5/datumoj/blob/f394021ec470e56d4faec2a2bb07e773932855b4/AGENTS.md#L67-L76) 为 datumoj 账户所有桶对象 Size 之和，1 GB=10^9 字节。#84 仍未合并，不能把其全部规则描述为已落地；#110 已明确采用 8.5 GB 停用定时条件。
- 这个单命名空间估算距离 9 GB 仅 196,574,763 字节。其他桶/前缀、采样时差、估算误差均未计入，不能据此宣称账户还可继续写入这些字节。没有账户级已鉴权盘点，账户总量与账单情况未知。

## 当前执行状态与安全处理顺序

核查时最新运行列表中没有进行中或排队的 Cloud run，#39 已完成。[main 工作流](https://github.com/gorou5/datumoj/blob/cc3aa3d87b02a673cff046a70e2b985ce6889ba3/.github/workflows/cloud-run.yml#L1-L7) 仍配置 `17 */3 * * *`；按 cron 的下一计划槽是 2026-11-07 00:17 UTC，实际启动可能延迟。工作流是否已被 UI 停用未获验证，不能仅凭 YAML 宣称当前一定启用。

1. 用户/维护者先明确暂停决定。临时 Disable workflow 可阻止后续定时，恢复时再 Enable；它也会影响手动触发，应在操作前说明。另一方案是经审核移除 schedule，仅保留 workflow_dispatch，但必须走授权的变更/合并流程。
2. 只读核实 datumoj 账户各桶对象 Size 总和、采样时间，以及未纳入该命名空间的占用。请补充这些技术数据，不需要发送账户标识、凭据或对象键。
3. 不为再量一次而触发生产采集，不降低 72 小时保护，不强删 raw/快照，也不把提额或更改保留期当成默认操作。当前 raw 无可清理候选，不能假设下一轮就能靠 raw 清理回落。
4. 暂停后的观测安排和一周验收需记录实际中断，不能把未连续稳定运行的一周标为通过；验收是否重置尚未决定。

## #114 与前次故障的边界

[#114](https://github.com/gorou5/datumoj/pull/114) 仍 open、未合并，head=0aa9f3a5ba4d06a58477ab263fed82f8d6c1328b；[CI #351](https://github.com/gorou5/datumoj/actions/runs/42296497783) success。它增加诊断和未发布告警保护，不处理容量。#39 使用旧 main 成功，不能证明 #38 的根因已修复。

此前 Muse 转交文件中“维护阶段撞上 40 分钟预算”的因果判断没有日志证据。budget_exhausted 与 maintenance_failed 同时出现不能证明前者导致后者；可区分的维护操作和实际错误类别仍未知。e-Gov parse_index_failed=1 也尚未确认为粘滞计数，不应沿用旧 JMA 结论。未公开任何未修安全细节。
