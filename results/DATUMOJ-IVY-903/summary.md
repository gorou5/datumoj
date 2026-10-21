# DATUMOJ-IVY-903 完成说明

自动验收通过；60 题在真实实现上达到全部五项默认门槛。待人工评审，未提交、未打 tag。

## 改动

- `src/datumoj/wiring.py`：沿用已接好的 AnalysisTools、HybridRetriever、SqliteCatalogStore、DuckDBObservationStore、SqliteManifest、FileRawStore；增加 `overrides["embedder"]` 注入点，默认仍为 OllamaEmbedder。增加无参 `build_tools()`，由 MCP 专用线程创建依赖。
- `src/datumoj/cli.py`：增加 `datumoj mcp`，调用现有 `mcp_server.main()` stdio 入口，默认工厂为 `datumoj.wiring:build_tools`，保留用户配置的 `DATUMOJ_TOOLS_FACTORY`；增加 `datumoj eval [--gate] [--report DIR]`，调用评测框架时传入 `manifest=`、`catalog=` 及真实工具层的出处还原函数。默认报告目录由 `tempfile.mkdtemp()` 创建，门槛失败时仅 `--gate` 返回 1。
- `tests/integration/test_full_eval.py`：完整语料初始化、真实依赖身份检查、全部题库及门槛、CLI 成功/失败退出码、MCP 工厂和内存会话、合成公告解析器合格测试。

生产代码仅改接线和命令入口。没有修改开发线模块、契约、已有测试或 fixture，也没有增加依赖。

## 语料与验证

语料独立于题目构造，直接保存 fixture 原始字节到临时 RawStore，再执行真实 `parse_pending` 及关键词/向量/目录/观测值写入。没有重新序列化来源正文，没有手工注入预期命中或答案。真实 LanceVectorIndex 使用确定性的 HashEmbedder（1024 维，与默认 bge-m3 维数一致），不调整 HybridRetriever 排名或相似度阈值。测试确认向量确实参与检索，且没有降级警告。

共 83 条成功快照：法令 51（25 份正文、22 份修订履历、4 份列表）、Dashboard 10、e-Stat 文件 3、CKAN 11、JMA 7、synthetic 公告 1。全部解析成功，无失败、无缺失解析器、无向量写入暂缺。83 条快照的路径、哈希、URL 和解析元数据见 `corpus.json`，流水线结果见 `parse.json`。

按 eval_extra README 合并真实 RegistryImpl：noingest 注册但无快照；fetchfail 只有 manifest-seed.yaml 中指定的 503 失败快照；unknown 未注册。合成公告仅使用测试专用 AnnouncementParser，并继承 ParserSuite 跑通四项合格测试。真实来源使用现有解析器与 CKAN 接线适配器。

题库 60 题：检索 30、数值 10、缺口 8、时间 6、注入 4、跨来源 2。评测未过滤任何题目，未覆盖默认门槛。

| 门槛指标 | 实测 | 门槛 | 结果 |
| --- | ---: | ---: | --- |
| Recall@10 | 0.961538 | ≥ 0.85 | 通过 |
| 数值精确率 | 1.000000 | 1.0 | 通过 |
| 数值出现率 | 1.000000 | 1.0 | 通过 |
| 缺口正确率 | 1.000000 | 1.0 | 通过 |
| 引用可还原率 | 1.000000 | ≥ 0.95 | 通过 |

另记录 MRR 0.823718、时间语义正确率 0.833333、注入隔离正确率 1.0。时间和注入指标不是框架的默认门槛，未将它们冒充为全题通过。

MCP 使用 608 的工厂入口，在专用线程重新打开真实存储。内存会话依次调用 search_docs、cite，命中日本国憲法 art=21，检查还原的 SourceText 与索引块原文一致，并校验 raw SHA-256。完整工具响应见 `mcp.json`。CLI 测试另确认 stdio 入口不在主线程提前构造工具。

测试默认把所有数据与报告写入临时目录；仅设置 `DATUMOJ_EVIDENCE_DIR` 时把集成证据写入指定目录。首次未设置该变量的验收后，工作区只出现本任务源码和新增测试的改动。交付证据运行显式设置该变量为 `results/DATUMOJ-IVY-903`。CLI 未指定 `--report` 的成功/失败报告路径也经测试确认为仓库外的临时目录。

## 验收命令与证据

- `make check T=tests/integration/test_full_eval.py`：9 passed，6 warnings，15.48s；原始输出见 `acceptance.log`。先在默认临时目录模式运行通过，再显式设置证据目录运行通过。
- `make check T='tests/test_cli.py tests/test_mcp_server_threads.py'`：10 passed，1.14s；输出见 `regression.log`。
- `git diff --check`：通过。

make 按现有机制回退到主仓库只读 .venv；没有安装依赖。6 个 warning 均来自现有 LanceDB `table_names()` 弃用提示，不在本任务 scope 内。

其他证据：`evaluation.json` / `evaluation.md`（含总体、分来源、分语言、逐题指标及完整失败清单）、`gate.json`（全部默认门槛通过）、`commands.json`（CLI eval 成功与失败退出码）、`changed-files.txt`（变更文件清单）。

## 保留的问题和人工验收

报告中保留两道未完全命中的题目：

1. `behavior-time-estat-period-publication`：e-Stat 人口文件没有文档检索命中，因此该题的时间语义检查失败。
2. `stat-estat-tokyo-ict-employees-2016`：e-Stat 经济普查文件没有文档检索命中；SQL 数值精确匹配和出处数值还原均成功。

现有 e-Stat 解析器只产出 Observation，不产出可检索的 Document/Chunk/Dataset；因此这两道题拉低总体召回，时间指标为 5/6。总体五项门槛仍全部通过。没有为补分伪造文档或改开发线模块。

完整语料指全部当前支持格式的成功录制样本及规定的 synthetic 公告；损坏/攻击 fixture、HTTP 404 响应、旧 Dashboard API 格式不作为成功语料入库，继续由各线原有测试覆盖。未读取或改写 scope 外的文件以修复这些情况。

未执行：全仓库测试、真实 Ollama/bge-m3 效果评测、H5/H6 人工抽查、人工合并和版本 tag；这些不属于本任务自动验收。未改测试门槛或题库。
剩余风险：上述两道 e-Stat 文档检索缺口仍在；HashEmbedder 结果不能替代真实 bge-m3 效果及引用支持率的人工验收。
