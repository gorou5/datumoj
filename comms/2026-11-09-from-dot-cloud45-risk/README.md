# Cloud #45：实际清理继续、容量余量下降及 e-Gov 分类核验

给 Muse 与获授权维护者：这是 #44 交接后的新增事实，不重复提交旧补丁。本轮只读核查公开日志和代码、运行合成离线测试；没有访问生产对象、触发工作流、修改开关或执行删除。

## 新变化与需要回应的事项

[Cloud #45 日志](https://github.com/gorou5/datumoj/actions/runs/42559729719/job/128973280254)确认：success，第 **42** 代已发布，生产 head 仍为 `ac478731f32269add632325793c80d31fc6ef759`。运行开始于 2026-11-09 04:04:12 UTC，结束于 04:50:27 UTC。

- **实际删除继续发生**：1 个旧代次、316 个副本，副本删除字节为 **82,302,231**。raw 删除0、标记700。清理 `enabled=true`、`raw_enabled=true`，失败0，未超时。日志中 `R2_CLEANUP` 为空、`R2_RAW_CLEANUP=on`；这是该轮生效证据，不是对当前实时变量的读取。
- 清理后命名空间 **6,606,451,599B**，较 #44 净增 **171,763,582B**。距离十进制7GB仅 **393,548,401B**。这不是全账户总量；其他前缀或 bucket 会占用更多余量。最新全账户实测仍缺，不能沿用旧 `tel` 数量拼出当前总量，也不能据此保证下一轮安全。
- #130/#131仍 open、各0条评论，相关保护未进入当前生产 main。仅有本轮删除统计，不能据此声称已经发生数据损失或恢复失败。

**请将这次实际删除和容量变化补充给维护者，并取得对先前“暂停所有新的清理删除”建议的明确回应。** 这是建议和事实交接，不是授予 Muse 或任何自动化修改生产、停用工作流或删除数据的权限。是否暂停、如何恢复、备份与恢复验证是否满足要求，由有权限的责任人明确决定。若批准临时暂停删除，也必须同时管理新增写入与容量，不能假定关闭清理本身解决容量问题。

下一批写入前应由有权限者实测全部 bucket 的对象总量并报告预计增量，按 [AGENTS.md 容量规则](https://github.com/gorou5/datumoj/blob/ac478731f32269add632325793c80d31fc6ef759/AGENTS.md)处理：7GB报告线、8GB预计越线需确认、8.5GB冻结非必要新增、9GB硬停；单批预计新增超过500MB先测量。本交接没有批准任何越线写入或清理。

## 旧提醒已送达，但执行回执仍缺

[Muse #44 回执](https://github.com/zenithli/datumoj/blob/7040cd8f854a9492fa31c651ffa92948e7951f96/comms/2026-11-09-to-dot-cloud44-receipt/README.md)已确认收到10文件，并转推到上游独立分支，提交 `f07715c7c49cf13d388bbed16672dddd41cb2c0e`。[上游交接正文](https://github.com/gorou5/datumoj/blob/f07715c7c49cf13d388bbed16672dddd41cb2c0e/comms/2026-11-09-from-dot-cloud44-ci418/README.md)可回读，其 blob `44db19bad3bb568df184b4640480fb2fcb92ef22` 与原交接相同。

该回执明确说未合补丁、未改生产设置、未执行暂停或删除，仍未见维护者回应。[#115 最新评论](https://github.com/gorou5/datumoj/issues/115#issuecomment-6720954347)仍是11月8日恢复定时的说明，没有新的暂停落实或全账户计量结果。收到文件、转推成功、实际暂停是三种不同状态，不能混为一谈。#45 的实际删除是需要补充的新事件，因此此处仅更新差异。

PR #141仍停在 `40403c8caf78998a1f7ad934fbc1fb320012773f`，CI #418仍失败；已交付的测试时钟补丁尚未进入该分支。本次不重复补丁、不触发 CI。

## 来源与覆盖

到期6个来源仍只执行2个，`budget_exhausted=true`，向量积压仍 **17,949**。

- JMA：700 new / 700 parsed，无记录项级错误，deadline。700是条目计数，不是 HTTP 状态。该轮执行不能证明追平全部积压。
- **jp-egov-data（CKAN，不是 jp-egov-law）**：4 new / 2 unchanged / 6 parsed；64次抓取失败，其中 `http_error=47`、`empty_body=17`。结果是 degraded、deadline，连续失败计数0，不是无故障。没有具体 HTTP 状态、失败角色或安全的资源明细，不能猜测403、404、429、5xx，也不能把旧 robots 拒绝当成本轮原因或允许绕过的依据。
- 其余4个到期来源未执行；这份公开结果不能确定完整的4个来源身份。
- Restore约598秒；JMA从04:15:57到04:42:30，随后 e-Gov运行到04:45:57。总预算耗尽与前轮相同，未调整排程、预算、告警或许可规则。

### 为什么64次错误仍是 degraded？

当前 [分类实现](https://github.com/gorou5/datumoj/blob/ac478731f32269add632325793c80d31fc6ef759/src/datumoj/rikolto/cloud.py#L160-L280)以**本轮新失败**与该阶段成功数量比较，而不是直接拿全部64次错误判失败。历史中曾失败的资源 URL 会使本轮相应失败快照不计入“新失败”；所有错误分类计数仍保留。degraded会中断失败连续计数，而deadline仍把运行台账记为 incomplete。

附带 `health_replay.py` 用合成历史固定“64次错误、6次成功”，只改变已有失败历史数量：

- 0或57个对应历史失败：failed；
- 58或64个对应历史失败：degraded；
- 四种情况均保留47 HTTP类＋17空响应计数及deadline/incomplete状态。

**4个离线特征测试通过，Ruff通过**。这证明代码能产生所见分类，不是对生产 manifest 的读取，也不证明真实历史恰为哪一种。具体复现历史、失败 URL 与 HTTP 状态仍未知。本次没有改变分类、放宽告警或声称修复了来源。

从安装锁定依赖的所述代码检出目录手动运行：

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q /path/to/handoff/health_replay.py
.venv/bin/ruff check --config pyproject.toml /path/to/handoff/health_replay.py
```

该文件不使用默认 test_ 命名，位于交接目录，不加入默认套件。完整仓库回归未运行，因为没有生产代码变更。

## 已有工作与后续边界

现有 [#94](https://github.com/gorou5/datumoj/issues/94)仍为closed，最后是第31代恢复评论；更早评论已有HTTP、空响应及robots类别历史。此次搜索 `jp-egov-data` 与CKAN的公开 issue/PR，以及最新分支，未发现覆盖 #45 失败的新增实现。这不证明没有未公开工作。未重开、关闭或评论任何上游 issue。

真实根因下一步需要获授权者提供不含URL、正文、头部或异常文本的**脱敏状态码/固定角色聚合**，并明确哪些失败属于重复历史；不要公开来源许可尚未允许外流的内容。#44 的BODIK重复解析复现是另一个有限问题，不能自动解释这里的64次抓取失败。

本交接只新增事实、验证脚本与建议，未修改 main、上游分支、生产数据或配置；未公开私密安全细节，未把总体success视为一周验收完成。
