# ADR 0007：观测值来源发布与固定代次读取

- 状态：已定（2026-10-28 人审通过，检查点 WISTERIA-901 / m0）
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
此时调用方可改用明确日期；WISTERIA-308 新增的 ReleaseEntry 消歧选择器见末节。
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

## 301 / 307 实现说明

### 本机布局与原子写入

保留原有 `parquet/<source>/<表键哈希32位>.parquet` current 文件名和表水位。
current 只含当前批次；footer 的 `datumoj.history_segments` 保存已提交的随机段名列表。
历史位于 `parquet-history/<source>/<表键哈希32位>/<随机32位>.parquet`，每次写入只新增
本批事件，不读取或重写旧段。段保存完整观测字段和原始 `_order_at` / `_order_seq`；
按完整身份加抓取排序键去重，重复事件以较后提交的段为准，A→B→A 的抓取事件分别保留。
空批次不产生事件段，但仍原子更新 current 和水位。

先独占发布完整段，再原子替换 current 及其段引用。current 替换失败时移除本次段和发布
别名；进程突然退出留下的未提交段不被 current 引用，也不参与历史查询。不做自动清理。
有序删表保留空 current、水位和历史引用；无序删除清除水位但仍保留历史。
被水位拒绝的旧写入不产生新事件段。

状态模块的发布路径契约保持不变。每段还在专用的
`parquet/zz-observation-history/<随机32位>.parquet` 命名空间有一个硬链接发布别名，
footer 的 `datumoj.history_table` 指明原来源/表文件。别名在 current 提交前建立，和写入
使用同一写锁，保证状态模块冻结副本时已包含全部引用；它不额外占用文件内容空间。
恢复时由别名重建独立历史目录，能够重复执行。current 扫描在目录层排除这个发布命名空间，
既不列举其中的段，也不打开历史目录。301 留下的历史专用文件在开库时登记为排除项。
无 token 的请求沿用 301 的隐式固定代次规则：已有发布时直接读派生最新视图，无发布时
只读 current；截至查询、保留和发布索引生成才扫描历史。

### 301 历史迁移

`check_history_migration(root)` 是只读检查函数，返回 `tables`（待迁移表数）和
`reclaimable_bytes`（删除冗余旧文件可释放的内容字节数，不含块舍入；仍被其他硬链接
持有的内容不计入）。它不创建库、不写迁移记录。
正常开库仅登记待迁移项；首次 `write_table` 或 `import_release` 在写锁内自动迁移，
避免状态发布/恢复的只读出处抽查改写清单副本。更早的无历史指针表在首次替换前补一个基线段。

对 footer 含 `datumoj.history_file` 的 current：

1. 将它引用的 301 完整历史写为一个不可变基线段。
2. 逐条比较完整身份、排序键及所有观测字段，连同事件重复次数核对；`_current` 是
   当次视图成员标记，不属于历史事件身份。不一致立即报错，保留所有旧文件。
3. 收集同目录中带 `datumoj.history_only` 且事件完全被基线段包含的文件。
   current 原子切换到新段，并记录待删文件名与已核对段的 SHA。
4. 再核对段 SHA 和每个待删文件的标记/包含关系，全部通过后才删除这些旧文件。
   最后原子移除迁移记录。未标记文件、其他表的文件和未被包含的历史文件都保留。

任何阶段失败都可重跑：提交前失败移除新段；提交后通过 current 的迁移记录续接，
已经删除的旧文件不再需要，基线段不会再生成一份。迁移不修改已保留代次的副本。

更早的缺列文件仍兼容：读取 schema 后给 `release_id` / `released_at` 提供 NULL。
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
`data/` 保存可核对的代次文件，`verified.json` 保存清单哈希、依赖文件大小/mtime_ns 和逻辑字节数。
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

保留边界按文件选择存储方式：raw、历史段（含旧历史专用文件）优先硬链接；SQLite、
current 和其他可变文件首次从在线库保留时复制，绝不链接到在线可变文件。内容 SHA 与
前一个已保留代次相同的存储文件可链接到前代副本（包括 SQLite/current），先核对前代
依赖的大小和 mtime_ns。空 DuckDB 描述按重建后实际文件 SHA 比较。硬链接失败则复制，
逐个文件在 `verified.json.file_storage` 中记录 `link` 或 `copy`；`store_hashes` 记录
可跨代共用的内容 SHA。顶层 `storage: copy` 只为兼容 301 的读取方保留，表示可变文件
从在线库的默认策略；实际方式以逐文件记录为准。

push 仍从实际发布副本保留，只有 live raw/历史段的 SHA 与该发布一致时才复用其 inode。
pull 继承旧保留目录时也优先链接，复制退路保留 mtime，旧 token 的轻量核对继续有效。
硬链接任何一端被改动，所有依赖它的代次都会因大小或 mtime_ns 不同而报
`GenerationUnavailable`，不回退到 live current。

`size_bytes` 是全部登记依赖的逻辑大小，不能当作硬链接后的新增磁盘占用；逐文件的
方式和 inode 可用于核算共用量。派生发布索引、最新视图和旧格式适配文件仍按代生成。
不做按时间或个数的清理，按 2026-10-22 的决定由另外的机制处理。
本实现仅消除新增历史的累计重写、代次的冗余内容副本，以及已经核对完全包含的 301 文件。

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

## WISTERIA-308：发布枚举和同代次只读视图

新增模型 `ReleaseEntry(source_id, release_key, released_at, order, table_key=None)`。
`release_key` 有来源发布标识时取 `release_id`，旧数据取 `snapshot_sha256`；
`released_at` 在此模型里表示排序日，旧数据使用 fetched_at 的 UTC 兼容日，
不表示补出了未知的来源发布日期。`order` 是原始持久化 `SnapshotOrder`。
JSON Schema 通过现有 `export_schemas` 导出到 `schemas/ReleaseEntry.schema.json`。

新增契约：

```python
list_releases(*, generation_token: str, source_id: str,
              table_key: str | None = None, timeout_s: float = 10) -> list[ReleaseEntry]
```

必须显式传固定 token；无效或不可用的错误与 `query_readonly` 相同，不回退最新。
空范围返回空列表，但仍先核对 token 和代次依赖。来源范围枚举读取该代次已保留的
发布索引；表范围只再扫描该表的历史段，SQL 聚合后才把发布项取入 Python，
不加载全部历史观测行。超时预算覆盖校验、扫描、排序和结果构造；缺发布日期、缺顺序
沿用发布感知查询的明确错误。

同来源、同发布键、同排序日的重复抓取合并，取该枚举范围内最大的 SnapshotOrder，
与原字符串截至边界一致。同日不同发布分别成项，同键跨日也分别成项。
按 `(released_at, order.fetched_at, order.seq)` 从早到晚；键完全相同时再按发布键
稳定排列，该稳定顺序不决定观测值的版本冲突。同日 A、B 的示例：

| source_id | release_key | released_at | order.fetched_at | order.seq |
| --- | --- | --- | --- | --- |
| jp-stat | A | 2026-04-01 | 2026-04-02T00:00:00Z | 1 |
| jp-stat | B | 2026-04-01 | 2026-04-02T00:00:00Z | 2 |

`query_readonly(as_of_release=entry, generation_token=token)` 新增接受枚举项。
先核对该项的来源、可选表、发布键、日和顺序在该代次内存在，再把该来源（以及可选表）
限定为闭区间 `(排序日, SnapshotOrder) <= entry`，最后才执行 SQL。
两个来源都叫 A 时也能各自截至；同一个 SHA 跨日重复时也能准确定位日期。
未知项报 `ReleaseNotFound`。枚举项不是认证凭据或代次租约，调用方应保存并传回枚举时
使用的 token；不能用其他代次重新解释它。原有 `None`、`date`、`str` 语义不变，
字符串跨来源或跨日期仍报 `AmbiguousRelease`，同一引用真实的排序冲突也仍报错。

新增组合入口：

```python
with open_generation_views(data_dir, generation_token=None) as views:
    token = views.generation_token
    entries = views.observations.list_releases(source_id="jp-stat", table_key="t1")
    result = views.observations.query_readonly(
        "SELECT value FROM observations", as_of_release=entries[0])
```

不传 token 时只固定一次当前发布；显式 token 严格使用该代次。观测查询、枚举、describe
和水位读取都绑定并重新核对同一 token，禁止切换。目录库和块库仅打开保留目录
`.observation-generations/<generation>/data/` 的 `catalog.sqlite`、`chunks.sqlite`；
打开前沿用 `verified.json` 的逐文件大小及 mtime_ns 校验，明确要求这两个文件有记录。
缺失、改变、schema 不符或无法打开均报 `GenerationUnavailable`，不访问在线库。

两个 SQLite 存储新增 `open_readonly(path, timeout=30.0)`，使用已发布的独立完整库文件，
以正确转义的 `file:` URI、`mode=ro&immutable=1` 打开，并设置连接的 query_only。
immutable 表示该连接读取保留快照；不用于带未合并 WAL 的活动写库。
不创建目录、文件或旁文件，不建表、不修复、不迁移、不更新版本，也不进入写事务；
只接受当前精确 schema 版本。所有公共写方法先抛 `PermissionError`。
读方法复用原实现，目录视图保留入口的多词 RRF 搜索。
上下文退出或显式 close 关闭两个 SQLite 连接；观测查询的 DuckDB 连接每次调用后关闭。
打开中途失败也关闭已经打开的连接。本接口不修改状态格式、代次保留机制或清理策略。
