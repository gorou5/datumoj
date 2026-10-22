# DATUMOJ-TACHIBANA-501 完成说明

自动验收通过，等待任务要求的人工抽查。未提交代码。

## 改动与方案

- `src/datumoj/index/fts.py`：按空白和标点拆分并去重查询词；无分隔符的长查询按不重复 trigram 搜索并计算覆盖率。默认门槛为 coverage `0.5`、BM25 下限 `0.01`，只有两项都低时过滤结果。`SqliteKeywordIndex` 可分别覆盖门槛。LIKE 命中没有 BM25 时按 0 分参与低分判断。
- `tests/index/test_keyword_relevance.py`：继承 `KeywordIndexSuite`，覆盖常见词低相关命中过滤、正常查询、trigram 覆盖、阈值覆盖，以及低关键词命中加低向量分时的 `zero_hits`。
- `src/datumoj/index/hybrid.py` 未需修改：现有逻辑已先按向量阈值过滤结果，并在有效命中为空时调用缺口判定器；新集成测试验证两侧都无有效命中时为 `zero_hits`。
- `docs/adr/0003-keyword-threshold.md`：记录阈值决策、IVY 评测校准、分数尺度检查和已知边界。
- `results/DATUMOJ-TACHIBANA-501/`：保存改动前/后的完整评测报告、阈值候选评测报告、验收日志和本说明。

实现只依赖现有核心契约、`datumoj.text.normalize` 和 `datumoj.testing` 假实现；未改契约、题库、fixture 或依赖。现有 `RetrieverSuite` 与新增测试中的 `KeywordIndexSuite` 均通过。

## IVY 评测校准

校准在离线完整语料上进行：83 条成功快照、60 道题。先记录本任务修改前基线，再扫描 coverage 0.25、0.5、0.75（BM25 下限 0.01），并对比 BM25 下限 0.1。Recall 按 `law.yaml`、`catalog.yaml`、`stats.yaml` 题目文件分别汇总。

| 配置 | 总体 Recall@10 | law | catalog | stats | behavior 缺口准确率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 修改前基线 | 0.961538 | 1.000 | 1.000 | 0.900 | 1.000 |
| coverage 0.25 / BM25 0.01 | 0.961538 | 1.000 | 1.000 | 0.900 | 1.000 |
| **采纳：coverage 0.5 / BM25 0.01** | **0.961538** | **1.000** | **1.000** | **0.900** | **1.000** |
| coverage 0.75 / BM25 0.01 | 0.942308 | 1.000 | 1.000 | 0.900 | 1.000 |
| coverage 0.5 / BM25 0.1 | 0.961538 | 1.000 | 1.000 | 0.900 | 1.000 |

coverage 0.75 使 `behavior-injection-citizen-notice-zh-window` 失去预期命中，新增正常题召回损失；coverage 0.25 和 0.5 均保持基线，故采用其中更严格的 0.5。BM25 下限 0.1 没改变本语料指标，选择更宽松的 0.01 保留边界命中的余量。`behavior.yaml` 的已入库来源零命中题都判为 `zero_hits`。

来源级结果中 `jp-egov-law`、`jp-egov-data`、`jp-stat-dashboard` Recall@10 都为 1.0。`jp-estat-files` 仍为 0.0，这是改动前已有的解析边界：e-Stat 解析器只产出观测值，不产出可检索块；按 `stats.yaml` 题目文件汇总的 Recall@10 为 0.900，达到目标且未下降。两道原有失败题仍为 `behavior-time-estat-period-publication` 和 `stat-estat-tokyo-ict-employees-2016`。

## 验收与评审核对

- `make check T=tests/index/test_keyword_relevance.py`：20 passed。
- `make check T=tests/index`：175 passed，30 warnings。
- `make check T=tests/eval`：69 passed。
- `make check T=tests/integration`：36 passed，6 warnings。
- `git diff --check`：通过。

所有验收输出保存在本目录的 `acceptance-*.log`。uv 不可用，`make check` 按仓库既有机制使用主仓库只读 `.venv`；未安装依赖。30 个索引告警及 6 个集成告警均是既有 LanceDB `table_names()` 弃用提示。集成 HTTP 传输在完整评测测试中被禁止，评测使用录制 fixture；本任务新增测试只使用 SQLite 内存库和本地假实现。

评审清单核对：变更仅在任务 scope；新增行为均有测试；关键词与检索契约合格套件通过；无联网测试；实现没有导入其他开发线模块。未改向量阈值和 RRF。

跳过/未核对：未在真实 Ollama/bge-m3 或更大线上语料上重校准，也未执行人工抽查。风险：阈值根据当前 60 道离线题校准，扩充语料或题库后应重跑评测；`jp-estat-files` 的已知检索缺口不在本任务 scope。
