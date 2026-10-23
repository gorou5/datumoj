# ADR 0004：证据身份与单调发布

- 状态：**提议**（由人审定）
- 日期：2026-10-23
- 契约任务：DATUMOJ-MAPLE-002
- 依据：`docs/plans/0.3-maple.md` 第 6.0 节，D-01–D-07、D-13。

## 身份与顺序

| 对象 | 身份与规则 | 审核问题 / 接入任务 |
| --- | --- | --- |
| blob | 原始字节的 SHA-256，只负责内容去重和完整性；不能替代资源或观察身份。解析前校验实际字节，损坏不得发布。 | D-01 / 202；D-09 / 109 |
| snapshot | `snapshot_id` 唯一标识一次观察，包括失败和重复内容抓取。重复抓取保留新行，不能按 SHA 合并。 | D-01 / 202；D-03 / 404 |
| 资源 | `(source_id, resource_url)`；URL 必须先用 `redact_url` 去凭据，保留其余 URL 字面形式；不把不同来源或 URL 的同字节混成一个资源。 | D-01 / 202；D-03 / 404 |
| 解析产物 | `(sha256, parser, version, context_fingerprint)`；相同 blob 只有上下文相同才允许复用。 | D-01 / 202 |
| 文档发布 | 每个 `doc_key` 一个 current 和持久化发布水位 `SnapshotOrder`。current 是对某个已暂存文档版本的选择；旧版本仍可按 SHA 取回。 | D-02 / 107 |
| 引用 | 新 Citation 必填 `snapshot_id`，同时保留 `source_id`、`resource_url`、`snapshot_sha256`、`fetched_at`、locator、数据日期和许可；三者须指向同一观察。仅 SHA 命中多个资源时抛 `AmbiguousCitation`，不猜最近 URL。 | D-03 / 404 |
| 观测值引用 | `ObservationRef(source_id, table_key, obs_id)`；`obs_id` 只在一个表里唯一，不能跨表回查。字符串形式是依次包含这三个字符串的紧凑 UTF-8 JSON 数组；`ObservationRef.parse(str(ref)) == ref`，引号、换行和任意分隔符由 JSON 转义。 | D-04 / 306 |

`SnapshotOrder(fetched_at, seq)` 按二元组比较，优先快照记录的带时区抓取时间，其次 manifest 的写入序号；不是解析或重试时间。序号从 1 开始、只在成功插入时分配，允许间隙；同一 manifest 内持久化且不复用。`snapshot_order` 对未知 ID 返回 None。同一时刻的不同表示按实际时刻比较。不同 manifest 的序号没有共同顺序，迁移重放必须保留原始顺序并重新建立对应水位。

`citation_for_chunk`、`citation_for_observation` 是既有调用链，暂保留 snapshot_id=None 的兼容输出；404 负责在确认派生数据与快照的实际关联后接入填充。旧 Citation JSON 可缺省为 None，新调用方应显式提供 snapshot_id。仅由 SHA 和来源校验仍不足以在多 URL 中选择正确快照，不能仅给旧助手的结果补一个未经核实的 ID。

## 解析上下文指纹

纯函数 `parse_context_fingerprint(snapshot, meta)` 返回以下数组的规范 JSON 的 UTF-8 SHA-256：

```text
[source_id, resource_url, content_type, meta]
```

JSON 对象递归按键排序，紧凑分隔符，不转义非 ASCII 字符；数组顺序保留；meta 缺省为 `{}`，仅接受有限 JSON 值。URL、媒体类型和 Unicode 字符串不另作归一化。这里的“规范”指本契约的 `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)`，不是另一个跨语言 JSON 标准。任何实现须按此编码；数字 `1` 与 `1.0` 保留其 JSON 表达差异。

指纹不含 snapshot_id、fetched_at、seq、etag、last_modified 或 SHA；SHA 单独在解析键中。相同资源、媒体类型和 meta 的重复观察复用解析结果；同字节不同来源、不同 URL、不同媒体类型或不同 meta 均不能命中。meta 内若含变化的时间或父页面身份，它就是上下文的一部分，必须重新解析；不能偷偷删除字段。

A→B→A 的最后一个 A 可以复用已有 A 的解析产物，但仍是新观察，必须以最后 A 的顺序重新决定 current 和引用，不能因为“解析过”跳过发布。该管线接入属于 202、107、404；本任务只提供键和发布原语。

## 暂存、发布与兼容

1. 新管线使用 `put_document(document, chunks, publish=False)` 暂存版本；不改变 current、水位或当前块的可见集合。相同文档版本重写仍整批替换其块，原有归属和重复 ID 校验保留。
2. 必需的库全部写入后，由 107 调用单一发布入口。`PublishState` 为 staged / published / failed；本任务定义枚举，不实现跨库事务或运行状态持久化。
3. `publish_current(doc_key, snapshot_sha256, order)` 对已存在的版本，在同一 SQLite `BEGIN IMMEDIATE` 事务内比较并更新 current 和水位。水位为空或 order 严格更新时返回 True；相等或更旧返回 False，无任何 current / 水位写入。版本不存在抛 `NotFoundError`，不推进水位。发布相同 SHA 的更新观察也推进水位并返回 True。
4. 水位按 doc_key 隔离，重启或另一连接必须读到同一水位。旧失败重试只补历史版本，不能越过更新水位。
5. `put_document(..., publish=True)` 是兼容入口，仍无条件成为 current；它不改动或清除已有水位。新管线必须统一使用单调发布入口，混用兼容入口不能保证 current 单调。没有提供隐含的解析时钟或从文档猜测抓取时间。
6. `ParseRecord.context_fingerprint` 和 `get_parse` 第四参数默认空串，仅兼容旧调用方。显式非空指纹查询绝不退回空串。`parse_stats` 优先精确匹配上下文；缺少匹配时暂保留空指纹记录的旧 SHA 统计行为，供旧管线继续运行。这种兼容统计不能证明某个资源已经在新契约下解析，202 / 503 接入时须使用上下文记录。

当前文档存储仍按 `(doc_key, snapshot_sha256)` 保留版本，未新增独立的上下文产物仓库。新解析键解决去重边界；同 SHA 不同上下文的产物隔离、快照关联及 A→B→A 的重发布由 202 / 107 / 404 接入。旧管线行为在接入前仍有审核指出的缺陷，不能把契约通过当作这些业务修复已完成。

## 证据、数据日期与管线状态

- D-05 / 307：`ToolResult.evidence_state` 是 complete / partial / unavailable，可独立于有无结果；`evidence_inputs` 是可 JSON 序列化的对象，记录输入表、快照和过滤范围。建议键为 tables（包含 source_id / table_key）、snapshots（snapshot_id 列表）、filters（查询范围）。这些可选字段缺省 None 表示旧调用方未报告，不代表完整证据。
- D-06 / 203：Document 的数据日期必须传入每个 Chunk，引用的时间口径依据来源日期；日期未知保留 `no_data_date`，绝不拿 fetched_at 顶替。
- D-07 / 502：先过滤再选择 top-k；若需要有界补取且预算用完，返回截断说明，不能据此断言 zero_hits。现有 `warnings` 可表达截断，本任务不新增检索策略。
- D-13 / 503：抓取成功但尚未解析、解析失败、needs_ocr 或索引未建时使用 `GapState.not_indexed`，避免误报 zero_hits。`PipelineStatus` 显式提供 `parseable_snapshots`、`parsed`、`parse_failed`、`needs_ocr`、`searchable_chunks` 和可空 `index_generation`（不透明代次字符串）。计数是当前查询范围的非负整数，允许状态重叠，不要求加总相等；代次未知为 None。`ToolResult.pipeline` 可空，503 负责实际统计和工具接入。
- 来源正文仍只放 `SourceText.content`，证据状态不降低内容信任边界。观测值缺失与注记规则不变，不补零。

## SQLite 升级与本地数据迁移

两份库的 schema 版本均升到 2，只支持 v1→v2 原子升级，未知版本拒绝打开。

- Manifest：在一个事务里重建 parsed，主键增加 context_fingerprint；所有旧解析行复制为空串指纹，保留状态和错误、相对写入顺序。**不删除、不改写任何 snapshots 行**，保留 snapshot JSON、meta、seq 和 AUTOINCREMENT 的后继顺序。新上下文查询不会命中旧行，下次由 202 重新解析。空串不等同于任何实际指纹。
- ChunkStore：在一个事务里增加持久化水位表 `published_orders`，保留全部 documents、chunks 和 current_versions。已有 current 的水位为 None；第一次显式发布可覆盖它，此后按水位单调比较。初始化、迁移和版本号更新任一失败均回滚。
- 这只是 schema 兼容迁移，不修复历史产物中已串错的 URL、表或 current。真实本地数据由 **MAPLE-900** 提供重放到新目录的流程：先只读诊断、保留旧目录与快照/raw；按原快照顺序重放，重新计算上下文并解析、暂存、发布、建索引；核对快照行数/身份、raw 哈希、观测值引用、current、水位、索引和出处，差异解释后由人批准切换。旧库不能原地清空或用新目录覆盖，失败时回到保留的旧目录。本任务没有访问或迁移真实本地库。
- 重放完成前不要把旧空指纹记录批量填写成“推测的”指纹，也不要将同 SHA 的最近成功快照回填为所有引用的 snapshot_id。

## 十个合成审核复现场景

各负责任务写通过的回归测试到 `tests/regressions/test_audit_<任务号>.py`；本任务不预写未修复的业务回归，不使用 skip / xfail。

| 场景 | 修好后的断言 | 审核问题 | 负责任务 / 回归文件 |
| --- | --- | --- | --- |
| 同字节不同资源 | 两个 URL 的上下文分别处理，产物和来源不串用。 | D-01 | 202 / `test_audit_202.py` |
| 同一资源 A→B→A | 最后 A 成为 current，引用指向最后 A 的观察；重复 A→A 复用解析。 | D-01 | 202 / `test_audit_202.py` |
| 旧失败快照重试 | 新版已发布后，旧版重试成功仅补历史产物，不倒退 current。 | D-02 | 107 / `test_audit_107.py` |
| obs_id 串表 | 两表同 obs_id 的出处分别由完整 ObservationRef 定位。 | D-04 | 306 / `test_audit_306.py` |
| 聚合无证据状态 | 聚合有结果而无法补全出处时明确 unavailable / partial，附输入范围。 | D-05 | 307 / `test_audit_307.py` |
| 同 SHA 引用指错 | 快照身份唯一定位 URL，只有歧义 SHA 时明确报歧义。 | D-03 | 404 / `test_audit_404.py` |
| 坏 raw 按旧哈希解析 | 篡改字节被完整性校验拒绝，不产生成功解析或发布。 | D-09 | 109 / `test_audit_109.py` |
| 先截 top-k 再过滤 | 合格结果不会因预截断丢失；预算耗尽有截断说明。 | D-07 | 502 / `test_audit_502.py` |
| PDF 块缺日期 | 每个块继承有效日期，日期过滤和引用时间口径正确。 | D-06 | 203 / `test_audit_203.py` |
| runs.jsonl 坏尾行 | 有效历史可读，损坏尾行被隔离并告警。 | D-08 | 108 / `test_audit_108.py` |

## 验证与代价

FakeManifest、FakeChunkStore 和两个 SQLite 实现共用合格测试；持久化实现另验证重开连接、双连接水位、v1 现场样本以及迁移中途失败回滚。纯函数测试验证稳定指纹、上下文隔离、非法 JSON、ObservationRef 特殊字符往返。Schema 由模型导出，不改 registry。

保留兼容入口使现有调用方可继续运行，但必须由后续任务完成业务接入；不提供 R2 代次或写入租约（MAPLE-001）。本 ADR 仍待人审定。
