---
id: DATUMOJ-MAPLE-501
title: 多语言检索：关键词相关性门槛在英语、法语上重新校准，结论补进 ADR 0003
project: DATUMOJ
status: ready
priority: 2
depends_on: ["DATUMOJ-MAPLE-201", "DATUMOJ-MAPLE-402", "DATUMOJ-MAPLE-403"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/index/fts.py", "docs/adr/0003-keyword-threshold.md", "tests/index/test_fts_multilingual.py", "tests/integration/test_multilingual_retrieval.py", "results/DATUMOJ-MAPLE-501/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "schemas/**", "eval/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/eltiro/**", "sources/**"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_fts_multilingual.py
    - cmd: make check T=tests/integration/test_multilingual_retrieval.py
    - cmd: make check T=tests/integration/test_full_eval.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-501/summary.md 完成说明（含基线和各配置的结果表）"]
  review: {by: other_family, checklist: ["日文、中文查询的拆分是否完全不变", "IVY 日语评测指标是否不低于改动前", "校准结果表是否真实（复跑能得到同样数字）", "是否调了向量阈值或 RRF", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
英语、法语的问题也能检索到对应资料，关键词相关性门槛在这些语言上同样有效；日语结果不退步。

# 背景
计划 `docs/plans/0.3-maple.md` 第 3 节目标 7、6.7 节；ADR 0003（关键词门槛：覆盖率 0.5、BM25 0.01，只在日语 IVY 语料上校准过）。
- `index/fts.py` 的查询拆分：按空白和标点拆成查询单元，覆盖率 = 命中块覆盖的单元占全部单元的比例。英语、法语问题里的虚词（the、of、de、la 等）也算单元，可能拉低覆盖率误删相关命中，或因常见词带回无关块。
- 依赖 201（英语全文）、402（法语目录）、403（多语言目录），它们的样本和入库代码是校准语料。
- 本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。不新增依赖；不改契约；测试全部离线；向量侧（跨语言靠 bge-m3）本任务不调。

# 要求
1. **校准集**：在 `tests/integration/test_multilingual_retrieval.py` 里用 201、402、403 的样本离线建索引，写至少 12 个英语 / 法语查询（每个写明应命中的块或数据集），另加至少 4 个应判 `zero_hits` 的查询（语料里没有的主题，但含常见虚词）。
2. **先测基线**：记下当前门槛下这些查询的 Recall@10 和零命中判定准确率，写进 summary。
3. **调整**：只在基线有问题时改，改法限于查询拆分和覆盖率计算（例如拉丁字母查询按词计、去掉一张很短的虚词表），不改 BM25 下限的含义、不改向量阈值和 RRF；日文、中文查询的拆分结果必须不变（加断言）。
4. **ADR 0003 加“补充（多语言）”一节**：校准语料、各配置的结果表、采纳的做法和理由；格式照原有“校准”一节。
5. `tests/index/test_fts_multilingual.py`：虚词处理、日文拆分不变、门槛参数仍可覆盖。
6. `tests/integration/test_full_eval.py`（IVY 日语语料）的各项指标不低于改动前；`make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不调向量模型和向量阈值。
- 不做查询翻译。
- 跨语言（日语问英文资料）靠向量，本任务只记录现状，不改。
