# Cloud #28 取消与发布后收尾诊断

核查时间：2026-11-05 01:02 UTC。固定代码：`f7e665e868df14b5ee17444d0b095f6ad508374e`。本材料仅记录只读证据与离线控制流实验；没有修改产品代码、运行生产任务、删除对象或宣称故障已修复。

## 本轮终态与已知结果

- [Cloud #28](https://github.com/gorou5/datumoj/actions/runs/41932584838) 已结束，API 为 `completed / cancelled`；job 从 2026-11-04 23:17:57 到 2026-11-05 00:48:17 UTC，约 90 分 20 秒。
- 工作流上限为 90 分钟，日志在 00:48:14 出现 `The operation was canceled.`。时间与超时上限吻合，但现有 API/日志没有明确区分上限触发与人工取消，不能写成已证实的取消原因。
- JMA：新增 420、解析 420，`parse_index_failed=1`；Gov Web：新增 1、未变化 183、解析 3，`parse_index_failed=3`。两个来源都出现 crawl `deadline`。没有新证据确认这四项失败的具体子类型。
- 合计新增 421、未变化 183、解析 423。e-Gov 不在本轮已观察到的来源中，不能据此判断恢复。
- 00:28:45 输出 `publish_end`，其后约 19 分 29 秒直到取消，没有 `round_end`、最终 JSON、最终 generation 编号或完整清理报告。
- `publish_end` 在 `finally` 内，即使 `push()` 抛错也会输出。已有发布计时不能代替读取 current 指针、不可变代次和对应账本的验证。**不能宣布第 25 代已成功发布，也不能宣布发布失败或数据丢失。**

证据：[job 日志](https://github.com/gorou5/datumoj/actions/runs/41932584838/job/126932953380)、[工作流](https://github.com/gorou5/datumoj/blob/f7e665e868df14b5ee17444d0b095f6ad508374e/.github/workflows/cloud-run.yml)、[publish 的 finally 及后续路径](https://github.com/gorou5/datumoj/blob/f7e665e868df14b5ee17444d0b095f6ad508374e/src/datumoj/rikolto/cloud.py#L609-L665)。

## 新发现：告警同步没有成功

日志明确为 `source_issues failures=1`、进程退出 1。Actions 界面却显示该步骤 success，因为工作流设置 `continue-on-error: true`；不能以绿色步骤判断同步成功。

云端进度写到 stderr，工作流的 `tee cloud-result.json` 只接 stdout；CLI 要等 `run()` 返回才写最终 JSON。本轮没有最终 JSON，因此告警输入未完成是高度吻合的失败路径，但未直接取得 runner 的文件，不把它当作已读取的事实。

只读查询证实，核查时最新来源评论仍是：
- [JMA #89，第 24 代](https://github.com/gorou5/datumoj/issues/89#issuecomment-6644748462)。
- [e-Gov #94，第 24 代](https://github.com/gorou5/datumoj/issues/94#issuecomment-6644748801)。
- [Gov Web #96，第 21 代](https://github.com/gorou5/datumoj/issues/96#issuecomment-6640042361)。

[进度输出](https://github.com/gorou5/datumoj/blob/f7e665e868df14b5ee17444d0b095f6ad508374e/src/datumoj/rikolto/cloud.py#L61-L91)、[CLI 最终输出](https://github.com/gorou5/datumoj/blob/f7e665e868df14b5ee17444d0b095f6ad508374e/src/datumoj/rikolto/cli.py#L157-L170)、[输入失败处理](https://github.com/gorou5/datumoj/blob/f7e665e868df14b5ee17444d0b095f6ad508374e/scripts/source_issues.py#L116-L127)。

## 新发现：发布后有不可见的重型清理阶段

`publish_end` 后，正常成功返回路径首先设置 generation 并调用 `StateManager.cleanup()`。它会再次校验并物化当前代次、枚举对象、读取历史代次，并逐代串行下载 manifest 块以规划保留依赖。

- [cleanup 主体](https://github.com/gorou5/datumoj/blob/f7e665e868df14b5ee17444d0b095f6ad508374e/src/datumoj/rikolto/core/state.py#L759-L822)没有自己的进度事件或时间上限；临时计时在最后恢复旧值，没有返回。
- [逐代 manifest 下载](https://github.com/gorou5/datumoj/blob/f7e665e868df14b5ee17444d0b095f6ad508374e/src/datumoj/rikolto/core/state.py#L737-L757)没有跨代次已验证块缓存，即使各代次共享相同块也会再次读取。
- `--no-cleanup` / `enabled=false` 仅关闭删除，仍完整规划并报告可回收量。`raw_enabled=false` 也不跳过上述规划。**关闭清理不能被当作已经验证有效的快速修复。**
- 后续仍有来源健康汇总、资源 close、租约释放和临时目录清理等路径。公开日志不足以定位 #28 停在哪个具体函数。清理是有代码依据的重点诊断对象，不是已证实的唯一根因。
- 不能把 `publish_timings.cleanup` 的约 0.198 秒当作此阶段总耗时。该字段来自 push 内部，后面还有独立的完整 cleanup。

对照 [#27](https://github.com/gorou5/datumoj/actions/runs/41921244819)：23:02:28 `publish_end` 到 23:16:53 `round_end` 已有 865.165 秒（14 分 25 秒）未分段收尾。本轮在发布开始前用时 3063.155 秒，比 #27 的 2539.329 秒多约 8 分 44 秒；发布本身为 1118.504 秒（18 分 39 秒），与 #27 的 1137.989 秒接近。实际预算压力集中在发布前延长和发布后缺少边界的收尾，而不是可凭日志断言的“本轮上传变慢”。

## 可复现的离线实验

使用 Python 标准库，不导入项目依赖、不访问网络、不读生产数据。脚本需指向上述固定 SHA 的源码副本：

```sh
python reproduce_missing_cloud_result.py --repo /path/to/datumoj
python reproduce_post_publish_tail.py --repo /path/to/datumoj
```

- `reproduce_missing_cloud_result.py`：直接加载原 `source_issues.py`，拦截 subprocess。空文件和截断 JSON 都返回 1、输出 `source_issues failures=1`，GitHub 调用数为 0；合法空健康列表作为控制返回 0。控制数据里的 generation 为合成值，与生产发布无关。
- `reproduce_post_publish_tail.py`：抽取原 AST 方法，使用内存替身。24 个合成保留代次仅共享 2 个唯一 manifest 块时，三种清理开关组合都执行 48 次模拟 GET；都调用物化，清理耗时不保留。另证明 push 抛错仍发出 `publish_end`。
- 这些实验验证控制流和可疑复杂度，不证明真实生产对象数量、网络延迟或 #28 的堆栈位置。不是完整 pytest、性能基准或修复验证。

## 下一步交接要求

1. 先在已有授权的只读存储视图或一致副本核实 current、对应不可变代次、来源账本与租约状态，只反馈代次编号、时间、摘要和固定状态。不要复制原始来源内容、凭据或对象 URL；不要盲重跑、强制解锁或删除“疑似孤儿”。
2. 在包含固定基线的独立代码分支补充安全分段计时：push 返回/异常、cleanup 校验/枚举/逐代依赖规划、close、租约释放。仅输出固定阶段、计数、耗时和验证状态，不输出对象键、来源 URL、异常正文。明确区分 publish attempt ended 与 publication verified。
3. 针对已确认的重复读取路径评估本轮内已验证不可变块的有界复用，并保留每块及整库的哈希/大小校验、72 小时保留条件、租约和 ETag 删除保护。不能为提速削弱校验或删除规则。须先做真实形态的受控基准，再决定是否实施。
4. 让最终结果与告警同步在合理收尾边界内可完成，取消/结果缺失也有明确、可见的固定分类；不要依赖 `continue-on-error` 的绿色外观，更不能借此关闭来源 issue。任何提前保存的健康结果都须区别未发布、已验证发布与未知状态。
5. 补测取消前后、push 异常仍有 `publish_end`、清理超预算不删未验证对象、部分清理/租约丢失、共享块缓存校验、缺失/截断结果、完整结果的告警同步。最后在实际提交 SHA 上跑项目套件及 CI，再做生产验证。

## 状态边界

PR #100 仍开放、head 未变，已有 CI #313 成功；该 PR 解决 enabled_since，不解决本报告的新收尾和告警交付问题。#88 未合并，已核查的 schedule runs 仍为 0，一周验收未开始。不得用本交接提交、离线实验或单一绿色步骤替代修复和验收结论。
