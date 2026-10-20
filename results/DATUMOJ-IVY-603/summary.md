# DATUMOJ-IVY-603 完成说明：分析工具层

## 变更文件
- `src/datumoj/analysis/tools.py`（新）：`AnalysisTools`，七个工具 `search_docs` / `search_catalog` / `query_sql` / `get_chunk` / `cite` / `version` / `coverage`
- `src/datumoj/analysis/glossary.py`（新）：`expand_terms(query)`、`load_glossary()`
- `src/datumoj/analysis/glossary.yaml`（新）：中英 → 日文对照，149 条
- `tests/analysis/test_tools.py`（新）：61 个测试
- `results/DATUMOJ-IVY-603/summary.md`（本文件）

## 验收
- `make check T=tests/analysis/test_tools.py` → `61 passed`
- 另跑了全量 `.venv/bin/pytest -q` → `751 passed`

## 设计要点（逐条对应“要求”）
1. **统一外壳**：每个工具返回 `ToolResult[Any].model_dump(mode="json")`：`data`、`gap_state`、`freshness`、`warnings`、`gap_detail`。参数不合法抛 `ValueError`；协议约定的错误（例如 `QueryRejected`、`QueryError`）原样抛出，由 604 转成 MCP 错误结果。
2. **依赖注入**：`AnalysisTools(retriever=, catalog=, observations=, chunks=, manifest=, raw=, registry=, parsers=None, data_dir=None, index_built_at=None)`，参数类型全部是 `contracts` 里的协议，903 只需要接线。测试只用 `datumoj.testing.fakes`。
3. **search_docs**：调 `Retriever.search`。`since` 转成 `date_range=(since, None)`。查询经过 `expand_terms` 追加日文词，实际用的查询放在 `data.queries` 里，模型能看到改写了什么。`gap_state`、`gap_detail`、`freshness` 都照搬 Retriever 的返回。
4. **search_catalog**：结果带 `state`（`ContentState`），资源逐个带 `state`；正文已抓过的资源附 `snapshot`（来自 `manifest.last_success`）。有 `catalog_only` 的结果时，在 warnings 里说明“目录查到不等于读到正文”。指定了注册表里没有的来源时返回 `out_of_scope`。
5. **query_sql**：只调 `ObservationStore.query_readonly`，本层不执行任何 SQL，是否只读由 store 把关，测试也覆盖了写操作、多条语句和 ATTACH 被拒的情况。返回 `columns`、`rows`、`provenance`（逐条带 `citation_for_observation`）和 `describe`。provenance 里出现多个不同的 `survey` 时给出口径警告（单一调查不报，两个调查报，测试两种都有）。结果列里没有 `obs_id` 时，警告“无法逐行给出出处”。
6. **get_chunk(chunk_id, context=1)**：取当前版本里的这个块，以及同一快照里前后各 `context` 个块（0–10），每个块都带出处。
7. **cite(snapshot_sha256, locator)**：从 manifest 找快照，再到 raw 取字节并先 `verify`，然后按来源 kind 和 Content-Type 用 `select_parser` 重新解析，按 locator 精确匹配块或观测值。下面这些情况都返回 `status: "not_found"`，原因写在 `gap_detail`：manifest 里没有、raw 缺失、哈希不一致、没有解析器、解析失败、定位不到。
8. **version()**：包版本、`git rev-parse HEAD`（取不到就是 null 并给出警告）、数据目录（参数，否则 `DATUMOJ_DATA_DIR`，否则 `./data`）、索引构建时间。
9. **coverage()**：列出注册表里的全部来源，每个给出状态（`ok` / `fetch_failed` / `not_ingested`）、新鲜度、失败计数和许可。来源已入库、但有资源最近一次抓取失败时，也会给出警告。
10. **来源文本标注**：来源给的文本一律包进 `SourceText`，包括块正文、标题路径、数据集标题/说明/发布者/标签/许可、资源名/格式、调查名/指标/地区名/注记、未施行修改里的文字字段，以及 SQL 结果里的**所有字符串单元格**（本层分不清哪一列是代码、哪一列是标签，所以一律包）。`warnings` 和 `gap_detail` 里只出现本层自己的说明、标识符（source_id、doc_key、obs_id）和校验过的日期。测试用 `_assert_marked` 检查：注入样本和来源文本不会出现在 SourceText 以外的地方。
11. **出处、许可与法令**：每条结果的 `citation` 都带 `time_basis` 和对应日期，以及 license、attribution、usage_scope。来源不是 `redistributable` 时（包括许可未核对），条目上标 `usage_notice: "仅供内部使用"`，并给一条汇总警告。法令结果从 `Document.metadata` 读 `asof` 和 `pending_amendments`（JSON 字符串），放在 `law` 字段里。有未施行修改时给出 warning。`asof` 缺失或 JSON 坏了也给 warning，不猜。

## 需要确认的问题（契约层面，未自行修改契约）
- **目录结果的出处无法还原到快照**：`Dataset` 没有指向它所在目录快照（CKAN 元数据响应）的字段，所以 `search_catalog` 的 `provenance` 只有 source_id、url、时间口径和许可，没有 `snapshot_sha256` 和 locator。资源这一级在正文已抓取时能给出快照。要做到“目录条目也能还原”，需要在契约里给 Dataset 加快照引用（例如 `snapshot_sha256` + `locator`）。请决定要不要开契约修订任务。
- **索引构建时间没有协议提供**：`version()` 改用构造参数 `index_built_at: Callable[[], datetime | None]`，由 903 接线时提供；不提供时返回 null 并给出警告。如果希望由索引协议自己报告，需要改契约。
- 对 `get_chunk` / `cite` 的 `not_found`，`GapState` 里没有对应的值，这里用的是 `gap_state=zero_hits` 加 `data.status="not_found"`，原因写在 `gap_detail`。如果需要独立的状态值，要改契约。

## 跳过 / 未核对 / 风险
- 对照表有 149 条，比“约 100 条”多。地名只收了评测会问到的几个。没有人工逐条核对译法。
- `search_docs` 会自动追加对照表里的日文词。只有在追加的词不是原查询子串时才追加，日文查询基本不受影响。但在 RRF 融合下，追加的词会扩大命中，排序可能因此变化。真实检索器上的效果没有验证（要等 903 和评测）。
- `cite` 只按 locator 精确匹配，不做前缀匹配（例如用 `art=27` 匹配 `art=27;para=2`）。真实解析器的重新解析成本、`discover_parsers(skip_broken=True)` 会跳过坏插件，这两点都没有在真实实现上核对。
- `version()` 和 `coverage()` 没有参数，所以没有“错误输入”可测。这两个工具改测的是信息缺失（索引时间未知、注册表为空）和来源异常（抓取失败、未入库）。
- 工具层没有新增依赖，也没有改契约、测试夹具和 scope 以外的文件。
