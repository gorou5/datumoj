# ADR 0002：网络存储与本机数据库的边界

- 状态：已决定（2026-10-22，人定；与下方“建议”不同处以“决定”一节为准）
- 相关：DATUMOJ-TACHIBANA-103；PLAN §4.4；TACHIBANA 计划 §6.6

## 决定（2026-10-22）

- raw 照本 ADR 放 R2；**索引跟随数据一起上 R2**（chunk / 关键词索引、向量索引、观测值 Parquet 等派生数据的一致副本），本机的库和索引是可以按需从 R2 重建的工作副本。这一点取代下文“近期保留本机向量索引”的建议。
- 同步（备份）频率**按来源分别设置**：有的来源要频繁抓取，有的只需周期性，周期长短各不相同，不用全局统一的一个周期。
- 实现另排任务（不在 TACHIBANA-103 里做）；不把在线数据库文件直接放在对象存储上这一条仍然成立。

## 建议（103 提出时的原文）

本阶段将不可变 raw 接入 R2，其他库、索引、Parquet、锁和运行记录继续放在
`StorageConfig.data_dir` 的本机磁盘。下一阶段优先实现一致性备份与恢复演练，
再根据多实例访问和容灾目标决定是否迁移数据库。本 ADR 不执行迁移、备份或部署。

不把 R2 挂载目录当作 SQLite / DuckDB 的在线数据库磁盘：对象存储提供对象级操作，
不能据此假定它具备数据库要求的随机页写入、文件锁和 WAL 语义。

## SQLite：manifest、chunk、关键词、目录

| 可选做法 | 可行性与代价 | 建议 |
| --- | --- | --- |
| 迁到 Cloudflare D1 | D1 提供基于 SQLite 的托管 SQL，但不是 `sqlite3.connect` 的远程文件路径。需实现协议适配、改写事务/批量操作，验证 FTS、扩展、SQL 方言、容量及请求限制；Python 进程还需通过 API 或 Worker 边界调用。存在调用延迟、平台依赖和鉴权成本。当前关键词索引依赖 FTS5，须核实目标 D1 的支持及行为。 | 云端运行、多实例共享需求明确后单独原型验证；本任务不宣称现有实现可直接迁入。 |
| 本机运行，定期备份至 R2 | 保留现有 SQL、FTS 和低延迟。使用 SQLite backup API 或停写后制作一致性副本；不能直接复制正在写入的主库而忽略 WAL。多库间需暂停写入或记录同一代次与恢复清单，避免 manifest 指向尚未备份的派生数据。 | 近期优先；恢复点最多落后一个备份周期，故障恢复需要下载、校验和重建索引。周期、保留期与恢复时限由人确定。 |
| 迁到 PostgreSQL 等托管数据库 | 适合多写入者与跨主机事务，但需新适配器、SQL/全文检索迁移、连接池及运维；有费用和迁移验证成本。 | 多实例写入确有需求时比较；不因 raw 上 R2 就同时迁库。 |
| 单写入服务持有本机 SQLite，对外提供协议 API | 保留文件锁与现有数据库实现；增加服务鉴权、网络错误、可用性和容量管理。 | 可作为过渡；仍需备份，不等于无状态部署。 |

备份应包含版本、内容校验和、完成标志、raw 前缀与快照引用、模型版本和各库的同代次清单。
先上传副本，最后发布完整代次清单；失败代次不能成为恢复入口。恢复到新目录并校验，
通过抽样出处还原后再切换。raw 的生命周期不能早于仍被 manifest / 备份引用的对象；
本阶段不自动删除对象，保留期及孤立对象清理由人另定。

## LanceDB

LanceDB / Lance 支持对象存储 URI，因而向量表放 S3 类存储具有可行性；
R2 的 endpoint、region、鉴权参数及多写入者提交机制须对锁定版本做专项验证。
不能只把当前参数改成 `s3://`：仓库的 `LanceVectorIndex` 先构造 `Path` 并创建本机目录，
模型元数据 `_meta.json`、`vectors-state.json` 也在本机。需一并处理元数据位置、
缓存、提交一致性、索引构建和失败恢复；对象请求及网络延迟会改变检索/重建成本。
建议近期保留本机向量索引，并记录模型与维度，允许从 chunk 重建。

## Parquet / DuckDB

Parquet 是适合对象存储的列式文件。DuckDB 的 httpfs 扩展可通过 S3 接口读取远端
Parquet（支持范围读取），也可将查询结果写出到对象存储；这不表示 DuckDB 的在线
`.duckdb` 文件能安全放在对象存储上。扩展安装、R2 endpoint 与凭证注入需要专门配置。

当前 `DuckDBObservationStore` 使用本机 `Path`、原子文件替换和本机 Parquet 视图；
查询还禁用了外部访问与自动扩展加载。直接换路径会破坏现有安全边界。
未来可由受信任的存储适配器加载固定对象清单，保持用户 SQL 只读且不能任意访问网络，
以不可变 Parquet 对象加版本清单发布整代数据，并另行设计压缩/合并与并发写入规则。
代价是网络延迟、小文件请求、缓存与跨文件一致性。建议先备份本机 Parquet，
再用实际查询负载验证直接读取 R2；本任务不放宽 SQL 沙箱。

## 依据与待验证项

仓库依据：`core/rawstore.py`、`wiring.py`、`index/fts.py`、`index/vector.py`、
`store/observations.py`。本任务仅对 S3 适配器完成 moto 本地 HTTP 合格测试，
没有验证真实 R2、D1、远端 LanceDB 或 DuckDB 的性能与兼容性。
以下是能力依据，具体服务配额、版本兼容、成本与目标部署仍需迁移任务核对：

- [R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/)
- [D1 SQL API](https://developers.cloudflare.com/d1/sql-api/)
- [SQLite online backup](https://www.sqlite.org/backup.html) 与 [WAL](https://www.sqlite.org/wal.html)
- [LanceDB storage](https://docs.lancedb.com/storage/)
- [DuckDB S3 API](https://duckdb.org/docs/stable/core_extensions/httpfs/s3api.html)

人类需确认：近期采用一致性备份方案与否、恢复点/恢复时间目标、备份保留期，
以及何时为 D1 / 远端向量与 Parquet 另开迁移及真实服务验收任务。
