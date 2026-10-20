# DATUMOJ-IVY-503 完成说明

## 实现与调用入口

- `src/datumoj/index/hybrid.py`：新增 `HybridRetriever(chunks, manifest, registry, keyword, vector=None)`，依赖均通过契约注入；`search()` 实现 `Retriever` 协议，`hybrid_search()` 提供任务指定的同参入口。
- `src/datumoj/index/coverage.py`：新增 `gap_state(source_ids, hits, *, registry, manifest)`，返回 `(GapState, gap_detail)`；检索器也提供 `gap_state(source_ids, hits)`。
- `tests/index/test_hybrid.py`：继承 `RetrieverSuite`，使用内存假实现、脚本排名和真实依赖的本地集成测试。未新增导出、配置或夹具；现有调用方只依赖 `Retriever` 协议，无需修改。

## 要求逐条核对

1. 每个有效查询分别调用关键词与向量索引，按每份排名的 `1 / (60 + rank)` 累加，不比较原始索引分数。按 `chunk_id` 去重，记录唯一的 `matched_by`，同分按 ID 稳定排序，最终最多返回 k 条。测试验证精确分数、多查询/双索引重复、单份结果重复、原始分数不可比、命中渠道、同分与 k。
2. 未配置向量，或向量搜索出现 `EmbeddingError`、`ModelMismatch`、`OSError`、`RuntimeError` 时，在 `warnings` 标注 `degraded=true`。任一子查询失败就丢弃本轮全部向量排名，继续所有关键词子查询；后续请求可恢复向量。测试覆盖未配置、四类失败、中途失败后的纯关键词分数与恢复；参数错误和关键词错误照常抛出。
3. 有命中返回 `ok`；无命中按 `out_of_scope → fetch_failed → not_ingested → zero_hits` 选状态，`gap_detail` 逐来源列出状态和失败原因。曾有成功快照但后来失败仍为 `zero_hits`；`ok / not_modified` 按契约视为已成功抓取。四种缺口各有两个额外用例，另验证混合优先级、无来源、空库、去重、抓取时间相同取后写记录。
4. 最近可查内容以成功快照线索表示：来源、资源 URL、快照 ID、完整 SHA256 和抓取时间；不把其他来源当成当前来源的命中。指定来源没有可回溯快照时明确说明；未限定来源的零命中给出注册来源中最近的成功快照线索。测试验证线索、缺失说明及 304 不覆盖可回溯正文。
5. 融合 `IndexResult`，通过 `ChunkStore.get_chunk()` 取当前块，使用 `Manifest.snapshots_by_sha256()` 找到同来源最新的 `ok` 快照，再用 `citation_for_chunk()` 组装完整出处和许可署名。缺少块/快照时跳过并警告；同 SHA 的其他来源不能冒充出处。正文仅进入 `SourceText`，无数据日期保留 `no_data_date`。
6. 日期区间按 `published_at / effective_at` 任一日期落在闭区间判断，支持开放端点，排除无日期块，拒绝反向区间；freshness 仅由最终命中所用快照计算。测试覆盖边界、双日期、无日期、多来源新鲜度和出处选择。

## 验收证据

- 亲自执行任务原命令：`make check T=tests/index/test_hybrid.py`。
- 结果：**54 passed, 2 warnings in 1.72s**，退出码 0。其中继承的 `RetrieverSuite` 共 13 个用例，其余 41 个为本实现测试。
- 完整输出：`results/DATUMOJ-IVY-503/acceptance.log`。Makefile 按既有机制回退到主仓库只读虚拟环境；未安装依赖。
- 警告来自已合并向量依赖的 LanceDB `table_names()` 弃用调用，未修改 scope 外文件。
- 变更清单：`results/DATUMOJ-IVY-503/changed-files.txt`。

## 边界自查

- 仅修改 scope 内三个代码/测试文件及本任务 results；未改契约、测试夹具、依赖清单、Makefile、文档或其他任务文件，未提交 git。
- 生产代码仅 import 标准库、契约/数据模型和本任务 coverage；测试额外使用 `datumoj.testing` 与 depends_on 允许的 501/502/102/104 实现。
- 全部测试离线：内存假实现、临时目录中的 SQLite/LanceDB 和确定性 FakeEmbedder；没有 HTTP 请求、联网服务、模型下载或新增夹具。
- 任务材料没有“评审中的高置信度质疑”章节。未调用其他 agent；跨模型评审及人工抽查由调度器后续安排。

## 未核对与风险

未做生产大语料性能或真实嵌入服务验证；每个查询/索引先取 k 个候选，日期过滤后可能不足 k 条。最近可查内容提供可回溯的快照线索，不保证与零命中的查询相关。
