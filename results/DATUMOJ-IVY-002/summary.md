# DATUMOJ-IVY-002 完成说明：契约二（接口协议、插件自动发现、假实现与合格测试）

## 验收
- `make check T=tests/core/test_contracts.py` → `193 passed in 0.41s`
- 没有跑全量 `pytest -q`：本环境只放开了上面那条验收命令（`make check T=tests`、裸 `pytest` 都被拒）。
  本任务没有改动已有的源文件，只把原来是空文件的 `src/datumoj/testing/__init__.py` 写上了内容，
  `test_models.py` 和 `test_smoke.py` 都不 import 它，按理不受影响，但没有实测。

## 变更文件
- `src/datumoj/core/contracts.py`（新增）：13 个协议、配套数据类型、错误类型、出处辅助函数、插件发现
- `src/datumoj/testing/fakes.py`（新增）：每个协议一个内存假实现，另有 `FakeCrawler`、`FakeParser`，以及假执行器 `drive_crawler`
- `src/datumoj/testing/contract_suites.py`（新增）：13 套合格测试
- `src/datumoj/testing/samples.py`（新增）：造合法测试数据的小工具（`make_spec`、`make_snapshot`、`make_chunk` 等）
- `src/datumoj/testing/__init__.py`（原来是空文件）：给合格测试注册 pytest 的 assert 改写，失败时能看到详细比较
- `tests/core/test_contracts.py`（新增）
- `results/DATUMOJ-IVY-002/summary.md`（本文件）

## 对照要求
1. **协议**（`typing.Protocol`，docstring 写了输入输出和错误约定）：`RawStore`、`Manifest`、`HttpFetcher`、`Crawler`、`Parser`、
   `ChunkStore`、`KeywordIndex`、`VectorIndex`、`Embedder`、`Retriever`、`CatalogStore`、`ObservationStore`、`Registry`。
   除 `Crawler`、`Parser`（插件基类，带类属性）外都是 `runtime_checkable`，合格测试第一条就检查 `isinstance`。
   通用错误约定写在模块开头：参数不合法抛 `ValueError`；约定好的业务失败抛 `ContractError` 的子类
   （`NotFoundError`、`CorruptedBlob`、`DuplicateError`、`SchemaVersionError`、`RegistryError`、`UrlNotAllowed`、`ParseError`、
   `QueryError`/`QueryRejected`/`QueryTimeout`/`TooManyRows`、`EmbeddingError`、`ModelMismatch`、`PluginError`/`PluginConflict`）；
   “取一个对象”的 `get*` 方法取不到返回 None；抓取失败不抛异常，体现在 `FetchResult.outcome` 里。
2. `Parser.parse(snapshot, data, meta=None) -> ParseResult`；`ParseResult` 含 `documents`、`chunks`、`observations`、`datasets`、
   `resources`、`warnings`。构造时检查 id 不重复、每个 chunk 的文档都在 documents 里；`check_against(snapshot)` 检查产物的
   source_id、snapshot_sha256（观测值还有 fetched_at）都和快照一致。
3. **插件发现**：`discover_crawlers(package="datumoj.crawlers")` → `{kind: 类}`；`discover_parsers(package="datumoj.parsers")` →
   `{(source_kind, content_type): 类}`；`select_parser()` 先按规范化后的媒体类型精确匹配，再退回 `"*"`。同键两个实现抛 `PluginConflict`，
   消息里写出两个类的全名。
   - 只收集**在类体里直接声明**了 `kind`（解析器是 `source_kind` 或 `content_type`）、并且定义在该模块里的子类：
     没声明 kind 的基类、只做小改动没重新声明 kind 的子类、从别的模块 import 进来的类都不算，避免误报重复。
   - **坏模块不会让人摸不着头脑**：默认扫完全部模块后抛一个 `PluginError`，一次列出每个坏模块的模块名、异常类型和消息（导入失败、
     语法错误、kind 不合法、解析器缺 `name`/`version`、content_type 带参数或大写）；`skip_broken=True` 时跳过坏模块、每个发一条
     `PluginWarning`，其余插件照常返回（CLI 可以用这个模式，不让一个坏采集器拖垮全部）。撞键在两种模式下都报错。
4. **假实现**：`FakeRegistry`、`FakeRawStore`、`FakeManifest`、`FakeHttpFetcher`（+`FakeResponse`）、`FakeCrawler`、`FakeParser`、
   `FakeChunkStore`、`FakeKeywordIndex`、`FakeEmbedder`（确定性哈希嵌入）、`FakeVectorIndex`、`FakeRetriever`、`FakeCatalogStore`、
   `FakeObservationStore`（每次查询建一个只读 SQLite 内存库：语句白名单 + authorizer + 超时 + 行数上限）。
   另有 `drive_crawler()`：给采集器测试用的假执行器（同一 URL 只抓一次、条件请求、写 raw 和 manifest、`max_files` 停止、分页失控时报错），
   201/203/304/305/401 不用等 105 就能端到端测采集器。
5. **合格测试**：`RegistrySuite`、`RawStoreSuite`、`ManifestSuite`、`HttpFetcherSuite`、`CrawlerSuite`、`ParserSuite`、`ChunkStoreSuite`、
   `KeywordIndexSuite`、`EmbedderSuite`、`VectorIndexSuite`、`RetrieverSuite`、`CatalogStoreSuite`、`ObservationStoreSuite`。
   实现方继承并覆盖 fixture（每个 Suite 的 docstring 写了要提供什么），用法写在模块开头。测的是行为：例如 manifest 按 fetched_at 而不是
   写入顺序取“最近一次”、失败记录原样保留；chunk 存储换快照后旧版本仍能按 sha 取回；关键词索引做 NFKC、一两个字的短查询也要能查；
   向量索引只嵌入新的或变了的块、换模型报 `ModelMismatch`；检索器的出处必须等于 `citation_for_chunk(...)`（带许可和时间口径）、
   四种缺口各自判对；观测值空值保持 NULL 和注记、`read_csv`/`ATTACH`/`SET`/多语句等都被拒；Key 不出现在抓取结果的任何输出里。
6. `tests/core/test_contracts.py`：13 个假实现各跑一遍对应的合格测试；契约模型与纯函数的测试；`drive_crawler` 的测试；
   插件发现用 tmp 包模拟（两个插件 + 重复键、坏模块、语法错误、skip_broken、kind 不合法、解析器声明不合法、通配 content_type）；
   另外用 6 个**故意写错的实现**（按写入顺序取最近、丢掉旧快照、不做 NFKC、空值补零、错误信息里漏 Key、缺口一律 zero_hits）
   确认合格测试能把它们抓出来，而不只是检查签名。

## 需要审查时重点看的设计决定（和下游任务文件的字面写法有出入的地方）
1. **`KeywordIndex.search` / `VectorIndex.search` 返回 `IndexResult`（chunk_id + 分数 + warnings），不是 `list[Hit]`**。
   DATUMOJ-IVY-501 第 3 条写的是 `-> list[Hit]`，但 `Hit.citation` 需要快照的 resource_url、fetched_at 和来源许可，单个索引手里没有；
   由 `Retriever`（503，本来就依赖 manifest 和注册表）用 `citation_for_chunk()` 统一组装 Hit。501 的任务文件需要相应调整，否则执行 agent 可能停下来问。
2. **`SourceSpec` 定义在 contracts.py**（pydantic 模型），不在 104 的 `registry.py` 里：105、201、401 等和 104 并行开发，都要用它。
   字段按 104 第 1 条，许可五项合成 `license: LicenseInfo`（复用“非 allowed 只能 internal_only”的校验）；`needs_key` 只接受 `env:NAME`。
   104 负责读 yaml、校验文件名和 id、检测明文 Key，再构造 `SourceSpec`。
3. **快照附加信息 meta**：201 要把“施行基准日”和“未施行修改”随快照保存给解析器用，但 `Snapshot`（契约一）没有这类字段。
   所以：`FetchTask.meta` → 执行器 `Manifest.record_snapshot(snapshot, meta)` 原样保存 → 流水线 `Manifest.snapshot_meta(id)` 取出 →
   `Parser.parse(snapshot, data, meta)`。meta 是可选参数，`parse(snapshot, data)` 的写法仍然成立。
4. **Manifest 比 102 列的多两个方法**：`snapshot_meta`（见上）和 `snapshots_by_sha256`（检索器、cite 从块的 sha 找回快照的 URL 和抓取时间）。
   解析状态表是 `record_parse(ParseRecord)` / `get_parse(sha256, parser, version)`；没有匹配解析器时 parser 写 `NO_PARSER`（`"-"`）。
5. **目录的入库程度**：`CatalogStore.set_content_state(resource_url, state)` 由流水线（601）在解析快照后调用；601 的任务文件里没写这一步。
6. **`chunk_id` 在同一文档的不同快照之间保持稳定**（建议 `<doc_key>#<locator>`），以配合 502“chunk_id + 文本哈希没变就不重嵌”。
   “当前版本”= 最近一次 `put_document` 的快照，所以流水线要按抓取时间顺序写入。
7. 检索器的 `date_range` 按块的 published_at / effective_at 过滤，两个日期都空的块在有日期过滤时排除；没指定 source_ids 且零命中时是 `zero_hits`；
   多个来源状态不同时按 out_of_scope → fetch_failed → not_ingested → zero_hits 取第一个。
8. `HttpFetcher`：只有 URL 本身不允许（非 http(s)、host 不在白名单、私网）时抛 `UrlNotAllowed`；重定向到不允许的地址记为 `network_error`。
   空正文、SPA 外壳由执行器判断（`Crawler.classify` 可覆盖），抓取器对 200 一律返回 `ok`。
9. 观测值视图 `observations` 的列就是 `Observation` 的字段（`OBSERVATION_COLUMNS`），`dims` 是 JSON 文本列；结果列里有 `obs_id` 时
   `provenance` 必须给全对应的 Observation（聚合查询没有 obs_id 就没有出处）。超行数抛 `TooManyRows`，不截断。
10. 插件的 kind / source_kind 统一要求匹配 `^[a-z][a-z0-9_]*$`（`egov_law`、`jma_feed`、`ckan` 等），`SourceSpec.kind` 也一样。
11. `CrawlerSuite` 要求“同一份回放再跑一次不产生新的 raw 内容”，`ParserSuite` 要求“同样输入两次解析结果相同、坏输入抛 `ParseError`”。

## 没做的 / 风险
- 按任务要求不写任何真实实现。
- `FakeObservationStore` 用 SQLite 而不是 DuckDB，只保证通用 SQL；值以文本存，`ORDER BY value` 会按字典序。导入 fakes 时会在 sqlite3 模块里
  全局注册一个名为 `DATUMOJ_FAKE_DECIMAL` 的类型转换器（名字专用，不影响其他代码）。
- 合格测试没有覆盖：真实超时（`ObservationStoreSuite` 里没有，因为写一个在 DuckDB 和 SQLite 上都“慢”的查询依赖方言，只在假实现上单测了）、
  HTTP 的限速/重试/重定向/robots（各实现自己测，103 的任务文件里已列）、50 个以上块的排序质量。
- 没跑 `make lint` 和全量测试（命令没放开）。
