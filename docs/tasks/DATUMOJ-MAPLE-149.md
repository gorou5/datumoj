---
id: DATUMOJ-MAPLE-149
title: 目录条目和观测值行投影进全文检索（不做向量），search_docs 能直接命中 entry=/row= 出处
project: DATUMOJ
status: doing
priority: 1
depends_on: ["DATUMOJ-MAPLE-148"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/pipeline.py", "src/datumoj/rikolto/index/**", "src/datumoj/wiring.py", "src/datumoj/rikolto/cli.py", "tests/index/**", "tests/test_pipeline*.py", "tests/regressions/test_audit_149.py", "tests/regressions/test_audit_145.py", "tests/test_change_detection.py", "tests/integration/test_state_roundtrip.py", "tests/integration/test_maple_m0.py", "tests/integration/test_maple_m2.py", "tests/integration/test_jp_files_e2e.py", "tests/integration/test_jp_files2_e2e.py"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "sources/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/analysis/**", "src/datumoj/rikolto/cloud.py", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "eval/questions/**", "src/datumoj/rikolto/eval/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**", "tests/integration/test_maple_eval.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_149.py
    - cmd: make check T=tests/integration/test_maple_eval.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "test_audit_149 打印的全部题库分数表", "投影块数量与块库增量估算"]
  review: {by: other_family, checklist: ["投影是否只复制解析器给出的字段，没有加翻译或题目文字", "投影块是否不进向量、不计入待嵌入数", "已有数据是否能不重新解析就补上投影", "IVY、TACHIBANA 题库是否没有掉分", "是否越界"]}
  human: sampled
max_attempts: 6
---
# 目标
解析器给出的目录条目（`Dataset`）和观测值行（`Observation`）进入全文检索，`search_docs` 和评测用的 `retriever.search` 能直接命中它们的 `entry=`、`row=`、`member=…;row=` 出处。这样 MAPLE 题库在正式入库流程上也能达标，不再只靠测试夹具。

# 背景
MAPLE-903 把所有来源放进同一个数据目录、走正式流程评测，MAPLE 题库不达标：
- 总体 recall@10 0.78（门槛 0.85）；
- maple_west 0.25，maple_compare 0，maple_multilingual 0.33。

没过的题全是 eu-eurostat、intl-wdi、fr-data-gouv、eu-data-europa、jp-census、jp-mhlw（NDB）的题，它们期望在检索结果里出现目录条目或观测值行的出处，结果判 `zero_hits`。

`tests/integration/test_maple_eval.py` 能单独通过，是因为它的夹具自己把 `result.datasets`、`result.observations` 投影成了检索块（见注释 "Mirror catalog/row fields only"）。正式流程（`pipeline.py` 的 `_dispatch`）只把 `result.chunks` 写进块库，目录进目录库，观测值进观测值库，所以检索不到。

云端规模（第 40 代）：块库约 2.68 GB，向量 0.78 GB，目录库 0.15 GB，观测值 Parquet 约 7 MB，待嵌入块约 1.8 万。所以投影块**只进全文检索，不做向量**：否则嵌入积压和 R2 都会大涨。

任务分支从 `feature/0.4` 拉出，合并回它。

# 上次失败（第 1–4 次）
四次都是 runner 跑满 1 小时被杀，验收命令一次都没跑到。调度器的 runner 上限是 1 小时，不能延长。参考耗时：`test_audit_149` 约 13 分钟，全量测试约 50 分钟。

**从 `refs/heads/task/DATUMOJ-MAPLE-149`（提交 84b82a2，第 4 次尝试）接着做，不要从头重写。** 主对话已在这个提交上核对过：
- `test_audit_149` 2 个全过，全部题库一起达标；
- `test_maple_eval` 6 个全过；
- `make lint` 0 条。

但全量测试有 8 个失败：
- `tests/test_change_detection.py::test_removed_document_without_chunks_uses_log_association`
- `tests/integration/test_state_roundtrip.py::test_state_roundtrip_and_incremental_second_source`
- `tests/integration/test_maple_m0.py::test_legacy_diagnose_and_replay`
- `tests/integration/test_maple_m2.py::test_five_sources_share_pipeline_and_mcp`
- `tests/integration/test_jp_files_e2e.py::test_each_source_fetch_parse_query_and_citation`（jp-ksj、jp-census）
- `tests/integration/test_jp_files2_e2e.py::test_each_source_fetch_parse_query_and_citation`（jp-mhlw）
- `tests/regressions/test_audit_145.py::test_search_docs_scores_against_pre_144_baseline`（stats、catalog 题库分数和基线不同）

逐个查清原因：
- 是投影带来的预期变化（例如块数变多、题库分数上升），才可以改测试的期望值；每一处改动都在交付说明里写明原因；
- 是投影弄坏了原有行为（例如删除文档时的变化关联、状态往返），就修代码，不改测试。

**时间预算：** 只跑上面 7 个测试文件、`tests/index/` 和 `test_audit_149`，各跑一遍，确认通过就提交。**不要在任务里跑全量测试**，也不要并行重复跑同一个慢测试；全量测试由调度器的验收去跑。

# 要求
1. **入库时投影。** 解析出目录条目和观测值时，同时写入检索块：
   - 目录条目：`doc_key = dataset_key`，locator 和快照取 `Dataset.locator`、`Dataset.snapshot_sha256`；没有 locator 的条目不投影。文本是标题、说明和标签。
   - 观测值行：`doc_key = dataset_key`，locator 和快照取 `Observation.locator`、`Observation.snapshot_sha256`。文本是这一行的字段，包括调查、指标、地区、时期、单位和数值；照 `test_maple_eval.py` 夹具的投影保留同样的信息。
   - 只复制解析器给出的字段，不加翻译、别名或题目里的文字。
2. **不进向量。** 投影块不嵌入，不计入 `vector_coverage` 的待嵌入数，`rikolto index --vectors-only` 和云端嵌入作业不会去处理它们。用什么方式区分投影块由你定，但不改 `core/` 的数据模型。
3. **同一个 doc_key 已有正文文档时**（例如同一个数据集既有说明页又有行数据），不能覆盖或丢掉正文块；也不能让一个当前文档混用多个快照，处理方式写进交付说明。
4. **已有数据能补上。** 已经解析过的快照不重新解析，也能从目录库和观测值库补出投影（例如 `rikolto index --rebuild` 时一并生成，或新增一个最小的 CLI 选项）。补投影要幂等，重复跑结果不变。
5. **新增 `tests/regressions/test_audit_149.py`：**
   - 用 IVY、TACHIBANA 已有来源的样本加 MAPLE 十个新来源的录制样本，放进同一个数据目录，经正式的回放抓取、解析、索引入库。**不在测试里自己做投影。**
   - 用全部题库（IVY、TACHIBANA、MAPLE）跑 `evaluate`，传入 `catalog`，全部门槛通过，打印各题库分数表。
   - 断言投影块没有进向量、待嵌入数不变。
   - 断言补投影的幂等性。
   - 可以参照 `refs/heads/task/DATUMOJ-MAPLE-903` 分支上的 `tests/integration/test_maple_m3.py` 搭建语料的写法，但不要改那个分支。
6. **报告块库增量。** 交付说明里给出样本语料上投影块的数量和块库字节增量，以及按云端目录条目数、观测值行数换算的估计（估算方法写清楚）。
7. `make lint` 0 条，全量测试全部通过（由验收去跑，见上一节的时间预算）；全量命令排除现存的 1 个 xfail，不新增 xfail 或 skip。
8. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改解析器、采集器和相关性门槛（`analysis/`）。`cite` 的修复在 MAPLE-148。
- 不改夹具、题库和评测框架；题目达不到门槛时不要改题。
- 不连真实 R2，不触发工作流，不访问网络和本机 `data/`。
- 不写账号、密钥、本机绝对路径。
