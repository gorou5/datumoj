# DATUMOJ-IVY-901 完成说明

自动门槛通过，等待任务要求的人工验收。20 道原始法令题 Recall@10 = **1.000（20/20）**，高于 0.85；MRR = 0.880556，前 3 条命中的出处还原率 = 1.000。失败题：无。

## 改动与接线

仅新增 scope 内的 `tests/integration/test_law_e2e.py` 和本目录交付证据。现有 `wiring.py`、`cli.py` 与 `sources/jp-egov-law.yaml` 已能完成链路，无需修改；未改开发线实现、契约、题库、既有测试或原始 fixture，未新增依赖。

测试通过 Typer CliRunner 执行项目注册的 `datumoj.cli:app`，依次调用 `crawl jp-egov-law` → `parse jp-egov-law` → `index --rebuild` → `search`（JSON 和人读格式）→ 再次 `crawl jp-egov-law`。每个命令重新调用真实 `wiring.build` 打开同一临时数据目录，并在完成后关闭 SQLite 连接，后续命令从落盘数据继续执行。

真实实现包括 PoliteHttpFetcher、ReplayTransport、EgovLawCrawler、run_crawl、FileRawStore、SqliteManifest、LawXMLParser（真实插件发现）、PipelineDeps/parse_pending/reindex、SqliteChunkStore、SqliteKeywordIndex、HybridRetriever、AnalysisTools/cite；catalog 与 observations 也由默认真实接线创建。没有使用 Fake 实现，没有注入检索答案或改写评测查询。

唯一依赖注入为回放 HTTP、固定录制日及不等待的测试时钟、`vectors=None`。这是 HybridRetriever 已支持的纯关键词模式，搜索结果保留 `degraded=true` 提示；未使用 HashEmbedder 或伪造向量。

## 要求核对

| 要求 | 结果与证据 |
| --- | --- |
| 1. 回放到 CLI 抓取、解析、索引、检索并带完整出处 | 22 部法令、45 个初始快照（1 列表 + 22 修订历史 + 22 全文）、2997 个块。日本国憲法第21条命中；校验来源、原始 URL、SHA256、抓取时间、art=21、基准日/时间口径及使用范围；`cite` 从 raw 校验哈希并重解析还原正文，人读输出也显示出处。所有快照校验 raw 哈希和成功解析记录。见 `commands.json`。 |
| 2. 第二次抓取无重复下载 | 新增 0、未变化 1、失败 0；只请求 robots 与列表，22 部法令的修订和全文全部跳过。manifest 仅新增一条列表观察；全文快照仍为22条。请求逐项断言，见 `requests.json`。 |
| 3. 使用701框架评测原始 law.yaml，Recall@10 ≥ 0.85 | 使用 `datumoj.eval.load_questions/evaluate/write_reports`，仅选原始 `law.yaml` 的全部20题，k=10；检索器为同一数据目录的真实 HybridRetriever。Recall@10=1.000，ja/zh/en 均为1.000；见 `evaluation.md`、`evaluation.json` 与逐题前10命中 `retrieval.json`。引用指标只返回重解析后的 SourceText 正文，避免把状态元数据误算成证据。 |
| 4. 未达门槛如实报告失败题，不改检索 | 本次没有失败题，未改检索实现。测试先写完整报告，再断言门槛；未来门槛失败仍保留逐题结果。 |

## 回放语料边界

先阅读 `tests/fixtures/README.md`、`tests/fixtures/egov_law/README.md`。使用目录内全部22部真实 law_data 及对应22份 law_revisions 原始录制字节，清单和SHA256在 `corpus.json`。录制的 `/laws` 页是全库开头及末尾的分页样本，法令ID与这22部全文语料不重合，不能直接用来发现评测语料。

因此生成 `laws_list.synthetic.json`：按文件名排序遍历全部录制全文，复制各文件的 law_info/revision_info 为一页22部法令的发现列表，current_revision_info 复制录制基准日的 revision_info。该列表是明确标识的人工测试数据，并非政府原始响应；不读取题库决定收录内容。`robots.txt` 用无正文404人工响应表示无规则。修订历史和全文响应均未经编辑，未改 protected fixtures。

基准日和测试抓取时钟固定于2026-10-20，以匹配录制 URL 的 asof；基准日来自显式 asof，未把抓取时间当缺失的数据日期。正常 HTTPTransport 在测试期间被禁止，未登记URL由 ReplayTransport 拒绝，不访问网络。

## 验收与交付

验收原文：`make check T=tests/integration/test_law_e2e.py`。结果：3 passed。uv在沙箱不可用，Makefile按约定自动使用主仓库只读 `.venv`；没有安装或下载依赖。完整输出见 `test-output.log`，变更文件清单见 `changed-files.txt`。`git diff --check` 通过。

未发现需另开修复任务的开发线问题，没有契约缺口。任务正文无“评审中的高置信度质疑”小节。没有执行 git commit、合并或打 tag；`v0.1.0-ivy-m1` 由人工验收通过并合并后处理。

跳过/未核对：未跑在线全库抓取、Ollama语义向量路径或人工验收；数值、缺口、注入等非法令题指标为N/A。风险：门槛仅证明固定fixture语料及纯关键词模式，发现列表为人工合成，不能据此推断线上全量数据表现。
