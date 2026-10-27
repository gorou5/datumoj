# ADR 0007：观测值来源发布与固定代次读取

- 状态：提议，待人审定
- 日期：2026-10-27
- 契约任务：DATUMOJ-WISTERIA-002
- 相关：[ADR 0004](0004-evidence-identity.md)、[ADR 0005](0005-state-generations.md)

## 两个独立维度

**数据代次**是 Rikolto 按 ADR 0005 条件发布的 `StateGeneration`，包含同一一致性边界下的
各库、历史观测值和原始快照。只有完成发布点的代次才可读取；仅上传的候选不算。
107 的 staged / failed 产物不得变为可查询发布。

**来源发布**是来源方的一次发表，例如一次 EDP 通报。一个数据代次内可保留同一逻辑
观测值的多次来源发布；来源发布编号、来源发布日期均不等于 Rikolto 代次号或代次生成时间。
这里的查询固定代次后再选择来源版本，不能用两者互相替代。

## 身份、发布日期与兼容

`Observation.release_id: str | None`、`released_at: date | None` 均缺省为 null。
标识须来自来源的发布记录或可核对的来源元数据，由后续解析器任务填充；本任务不伪造
发布标识，也不修改解析器。`released_at` 是来源发布日期，与统计期间、抓取日不同。

逻辑观测值继续用 306 的 `ObservationRef(source_id, table_key, obs_id)`。
具体版本用继承该引用键的 `ObservationIdentity`，增加 `snapshot_sha256` 和可空 `release_id`。
`Observation.identity` 返回它；`str` / `parse` 是五项紧凑 JSON 数组：

```json
["gr-example","edp","deficit-2009","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","notice-1"]
```

禁止仅按 `obs_id` 关联或去重；引用键隔离来源和表，完整身份还隔离快照与发布。
解析结果和写入批次只拒绝重复完整身份，允许同 obs_id 的不同发布/表/快照。
同一完整身份可有多次抓取观察，历史须同时保留它们的 fetched_at 和持久化 SnapshotOrder；
不以最后一次抓取覆盖首次出现，否则 A→B→A 的早期截至查询会丢失 A。
同一身份/同一 SnapshotOrder 重写幂等；已经固定的代次不受影响。
不同 SHA 即使 release_id 相同也保留为不同快照版本。
`write_table` 仍替换 current、保留原有水位和删除约定，但成功写入的历史发布不得随替换删除。
旧快照重试被水位拒绝时不推进 current，也不由该入口额外写入历史。

没有 `release_id` 的旧数据以快照 SHA 作为**查询用的发布标识**、以 `fetched_at` 的 UTC 日作为
**兼容排序日**，并用原始 `SnapshotOrder` 区分顺序。即使单独有 `released_at`，无标识时仍遵循
快照规则。不回填模型字段，不把抓取日写成已知来源发布日期；数据日期未知仍为
`no_data_date`，出处仍还原到快照。相同 SHA 的重复观察共享完整身份，但各自保留排序事件。
相同 SHA 跨日重复时，仅传 SHA 不能定位唯一截至日，抛 AmbiguousRelease；调用方须传明确日期。

有显式 `release_id` 却缺 `released_at` 的记录可存储，但发布感知读取抛
`ReleaseDateUnavailable`；不得用抓取日猜其来源发布日期。缺持久化 `SnapshotOrder` 时抛
`QueryError`。301 需要通过原 manifest 还原旧记录顺序；本任务不迁移数据。

## 截至查询

`query_readonly(..., *, generation_token=None, as_of_release=None)` 的处理顺序：

1. 原子固定一次有效数据代次并验证其可用性。
2. 在该代次的完整历史中按截至边界筛选，每个 `ObservationRef` 取最近发布。
3. 在所选版本组成的 `observations` 视图上执行 SQL，最后才聚合、排序和限制行数。

`as_of_release` 允许：

- `None`：每个引用的最新来源发布。
- Python `date`：包含该日期全天的闭区间；按来源发布日期比较，旧数据按上述 UTC 兼容日。
  同一天多次发布按 `SnapshotOrder(fetched_at, seq)` 排序，时间先于写入序号。
- 非空字符串：该代次内的 `release_id`，旧数据则为快照 SHA。先定位发布边界，
  再包含至该边界的所有发布。一个标识在同一来源、同一发布日期可以覆盖多表/多行和
  重复抓取，边界取这些记录最大的 `SnapshotOrder`。同日更晚的其他发布被排除。
  不按标识字典序排序。

排序键为 `(来源发布日期或兼容日, SnapshotOrder)`。字符串边界同样先比较发布日期，
因此较早发布但更晚抓取的数据仍在日期较晚的边界内。`SnapshotOrder` 只在同一 manifest
可比，一个查询代次的发布历史须来自该排序域；不能混用独立 manifest 的序号。
同一引用若有不同完整身份却完全相同的排序键，抛 `AmbiguousRelease`，不依赖容器插入顺序。
截至边界之前不存在的引用不返回，也不补零。
默认无截至查询还遵守该代次的 current 引用集合；已被整体替换移除或删除的引用不复活。
显式截至查询访问保留的发布历史，不把后来的 current 删除当作来源方发表的空值；
本版本没有来源撤回/删除事件模型。

找不到标识抛 `ReleaseNotFound`；标识跨来源或对应多个发布日期抛 `AmbiguousRelease`。
此时调用方可改用明确日期；当前接口不提供跨来源标识消歧选择器。
解析日期字符串不会自动进行：`"2026-04-01"` 是标识，日期应传 `date(2026, 4, 1)`。
带时间的 datetime、空标识及其他类型是非法参数。
边界定位和版本选择先于 SQL；SQL 的 WHERE 不能用来消除发布标识歧义，
也不能隐去缺失发布日期或顺序的历史记录错误。

## generation_token

`pin_generation()` 原子返回当前已发布代次的 token；没有有效代次抛 `GenerationUnavailable`。
格式为三项紧凑 JSON 数组：

```json
["rikolto-generation-v1",7,"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
```

`GenerationToken` 绑定严格正整数 `generation` 与 `manifest_sha256`。
后者是**实际发布的 StateGeneration 清单字节**的 SHA256，不是对象 ETag，
也不对读取后的 JSON 重新排版再哈希。调用方保存和传回不透明字符串；格式不含路径、
主机或凭证，不承担认证或授权功能。假实现以 `model_dump_json()` 的 UTF-8 字节作为其发布清单。

无 token 时每次调用在开始原子固定最新已发布代次；长运行应先获取一次 token，
所有分页、查询及截至读取均传同一 token。运行中发布新代次不改变旧 token 的含义。
读取历史代次不能重新访问在线 current 库，也不能仅用最新代次的清单哈希替换旧哈希。

- token 格式/版本非法、清单哈希不匹配：`InvalidGenerationToken`。
- 代次不存在、尚未发布、清单或其任一必需依赖已不可用：`GenerationUnavailable`。
- 底层其他错误保持明确错误，不能吞掉或伪装成功。

以上错误**一律不回退到最新**，SQL 无匹配结果也必须先验证 token。
没有时长到期机制；保留期间持续有效，但 token 不是永久保留租约。
301 须检查清单真实性、历史有效发布链和全部依赖的可用性。
本任务只定契约，不实现远端发布验证、保留或清理策略。

## 结果与证据

发布感知 `QueryResult` 给出所读 `generation_token`，并以 `row_evidence` 与 SQL rows
一一对应，每项含 `generation` 和完整 `ObservationIdentity` 列表。
逐行关联须选择五个未经变换的完整身份列：`source_id, table_key, obs_id,
snapshot_sha256, release_id`；去重按完整身份、按首次出现排序。
聚合、计算结果或缺身份列无法证明关联时，身份列表为空，不能猜测一个代表观测值。
所有行即使无可证明身份，也必须带所读代次号。

证据状态直接复用 307 的 `EvidenceState`，不增设发布专用状态：逐行证据齐全为 complete，
部分可证明为 partial，不能证明（包括假实现不追踪 SQL 聚合血缘）为 unavailable；
空结果可为 complete，但不表达零值。`evidence_inputs` 记录原 SQL、所读 generation 及
`as_of_release`（日期用 ISO 字符串，无条件为 null），让聚合的输入范围包含截至条件。
实现若能独立证明聚合的全部输入，可按 307 给 complete；不能把空逐行身份当作已证明输入。

新增结果字段保留空缺省值，仅兼容未接入 301 的存储/分析实现；它们尚不提供固定代次保证。
假实现未发布任何代次且没有截至参数时保留旧接口行为，便于现有离线测试继续工作。
一旦显式发布代次，查询只读已发布副本；截至查询无已发布代次则报错。
生产存储、query_sql、MCP、解析器和 Eltiro 契约均未修改；301 接入后必须填全上述元数据。
`ObservationReleaseStore` 在原 `ObservationStore` 上声明 `pin_generation` 能力；
旧实现的运行时协议检查不因暂未接入该能力而失败。
旧实现使用的 `OBSERVATION_COLUMNS` 列集保持稳定，避免直接查询历史 Parquet 时要求不存在的新列。
发布感知实现使用 `OBSERVATION_RELEASE_COLUMNS`，301 负责在读取旧文件时补可选列 NULL，
不得补观测数值零。

## 验证与审定

`ObservationReleaseSuite` 是独立可复用合格测试，301 可提供 store、publish、discard 夹具继承。
它与旧 `ObservationStoreSuite` 分开，避免让尚未接入发布能力的存储实现假装通过新语义。
假实现和审核回归均继承新套件，覆盖历史、同日排序、跨表/来源身份、固定 token、
未发布写入隔离、哈希错误、代次不可用、旧快照发布、聚合证据和缺失元数据错误。
JSON Schema 按模型重新导出并与仓库逐字比较。所有样本人工构造、测试离线。

人类需审定日期/标识边界、跨来源歧义和历史代次失效规则；测试通过不代表生产存储已支持。

## 301 实现说明

### 本机布局与原子写入

保留原有 `parquet/<source>/<表键哈希32位>.parquet` current 文件名和表水位。
current 只含当前批次；文件 footer 的 `datumoj.history_file` 指向同目录内一个不可变的
历史 Parquet，文件名仍是 32 位十六进制（兼容现有状态副本的文件名校验）。历史文件带
`datumoj.history_only` 标记，不作为 current 表读取。历史保存完整观测字段、每行原始
`_order_at` / `_order_seq` 以及 `_current` 引用成员标记；A→B→A 的各次抓取事件分别保留。
第一次写入可以只保存 current，下一次替换时把旧行纳入历史。

写入先生成历史文件，再原子替换 current 和 footer 水位/指针；替换失败删除新历史文件，
旧 current、水位和历史均不变。有序删表保留空 current、水位和历史指针；无序删除清除
水位但仍保留历史。被水位拒绝的旧写入不产生历史。读取 current 时忽略历史专用文件。
历史文件采用状态模块已经支持的 Parquet 路径，push/restore 不需要新增格式。

旧文件无需迁移：读取 schema 后给 `release_id` / `released_at` 提供 NULL。
顺序优先读 footer 已持久化的 SnapshotOrder；更早的文件按来源、SHA 和确切 fetched_at
从该代次的原始 manifest 恢复序号，不用最新的同 SHA 抓取替代。保留时为旧副本生成
`.release-legacy/` 派生 Parquet（不改原文件），后续查询不在 Python 中加载 manifest 历史。
无法恢复时，发布感知查询
抛 QueryError；没有代次时仍允许按旧行为读 current，包括没有顺序的文件。

### 保留、校验和查询成本

wiring 在本机 push 成功后恢复**实际已发布副本**并保留；restore(local) 完成时保留；
CLI `pull` 在已校验目录安装后保留，并继承前一目录已保留的代次。
有效性依据状态模块从 current 可达的发布链，候选或 staged 产物不能自行变为发布。
保留目录为 `.observation-generations/<generation>/`：`manifest.json` 保存实际发布清单字节，
`data/` 保存独立文件副本，`verified.json` 保存清单哈希、依赖文件大小/mtime_ns 和磁盘字节数。
`current.json` 原子切换 token；数据目录另保留已发布标记，避免保留目录丢失后误读 live current。
remote raw 模式的 restore 不创建可固定读取的本机代次；须使用 local raw 恢复。

完整存储校验、raw SHA 校验及 staged 状态检查发生在保留边界：push/restore/pull 复用状态
模块已完成的校验，不在 pin 或 query 中重新下载或哈希 raw。保留器独立使用时执行一次
完整校验。保留时还生成派生 `.release-index.parquet`，只记录发布边界与元数据错误/排序
歧义，避免每次查询遍历全部历史行。另生成按来源/表/引用排序的 `.release-latest.parquet`，
供无截至条件的查询及兼容工具元数据查询直接读取，避免这些请求反复对全库历史选版本。
两个派生文件都列入依赖状态记录。

查询仅哈希清单字节，并逐个 stat 核对登记依赖的大小和 mtime_ns；不同即报错，不重做
全量校验，也不自动修复或回退。只扫描登记的 Parquet；目录里额外出现的文件不参与查询。
截至条件和版本选择在 DuckDB 惰性视图里执行，SQL 的表/引用过滤可下推，历史行不会全部
载入 Python。全局元数据错误由派生发布索引检查，SQL WHERE 不能隐藏它们。
请求预算从代次加载之前开始，覆盖轻量核对、选版本、SQL 和出处回查；DuckDB 操作同时
由 interrupt 定时器限制，预算用尽抛 QueryTimeout。

**没有任何已发布代次的本机数据目录**，无 token、无截至条件时继续读 current，行为不变；
显式 pin、token 或截至条件要求已发布代次。已知发布但保留资料损坏时不能回退 current。
固定代次 token 没有自动过期，但不保证永久保留。

每个代次使用**复制**，不使用硬链接，避免 live SQLite/current 改写影响旧 token；
单代磁盘量为清单存储副本的重建文件 + manifest 引用的全部 raw + 派生发布索引 + 少量
清单/校验记录（以及最新视图、旧文件适配副本）；精确数据字节数见 verified.json 的 size_bytes。各代次没有去重和自动清理。
每次替换生成的旧历史文件暂不清理，也会计入状态副本；清理策略留给后续任务。

### 导入与入口

`rikolto observations import-release --source <id> --file <path> --release-id <id>
--released-at <YYYY-MM-DD>` 必须显式给出来源发布信息。文件按来源 max_total_bytes 限制
读取，先存 raw，然后仅使用来源注册采集器的本地 plan 提供 URL/meta、现有注册解析器
核对内容；不发 HTTP、不依赖文件名。只有唯一成功且含观测值的解析匹配才导入，否则报错。
尚未列在该来源计划里的存档不能猜测其 URL/表标识，须先由来源任务提供计划元数据。
原始 parser meta 原样保留，使出处可重放；发布标识和发布日期写在观测行里。

导入按表比较 `(来源发布日期或兼容 UTC 日, SnapshotOrder)`：只有整批导入行都比当前表
的最新行新，才整体推进 current；否则只追加历史，current 引用和水位不变。
缺少已知发布日期的显式 current 发布不能用抓取时间猜测。导入使用同一个 manifest 的
SnapshotOrder 域，并记录 staged/published 状态；失败留下明确 staged 状态阻止状态发布。

分析 query_sql 接受 Python date 或发布标识，generation_token 接受不透明字符串。
MCP 日期用 `as_of_release={"date":"YYYY-MM-DD"}`，字符串始终表示发布标识；可同时传
`generation_token`。CLI SQL 用 `--as-of-release <id>` 或 `--as-of-date YYYY-MM-DD`，
并可传 `--generation-token <token>`。不传新参数时兼容旧调用。

不传新参数的 analysis/MCP 调用保持旧响应形状和三列引用的兼容出处；内部回查固定同一个
隐式 token，避免请求内代次漂移。显式使用新参数时才暴露 generation_token、row_evidence
和来源发布字段，并采用五列完整身份的严格证据规则；聚合和不完整身份不猜测出处。
