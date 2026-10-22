---
id: DATUMOJ-TACHIBANA-501
title: 关键词检索的相关性门槛：只剩低相关命中时判为零命中
project: DATUMOJ
status: todo
priority: 2
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/index/fts.py", "src/datumoj/index/hybrid.py", "tests/index/test_keyword_relevance.py", "docs/adr/0003-keyword-threshold.md", "results/DATUMOJ-TACHIBANA-501/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/TASKS.md", "docs/plans/**", "docs/tasks/**", "docs/acceptance/**", "docs/references/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/integration/test_full_eval.py", "eval/**"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_keyword_relevance.py
    - cmd: make check T=tests/index
    - cmd: make check T=tests/eval
    - cmd: make check T=tests/integration
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-501/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
问资料库里没有的东西时，关键词检索不再因为常见词命中无关内容而判为 ok，而是判为 `zero_hits`；正常问题的召回不下降。

# 背景
开发线：E 检索线。IVY 验收的遗留缺口（计划 6.5 节）：向量一侧已有按模型取值的阈值（`index/hybrid.py` 的 `MIN_VECTOR_SCORE_BY_MODEL`，ADR 0001），关键词一侧（`index/fts.py`，FTS5 trigram + bm25，标题加权）没有门槛。IVY 的评测题在 `eval/questions/`，门槛测试在 `tests/integration/test_full_eval.py`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-501/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 关键词命中加相关性判断：把查询拆成词（按空白和标点；没有空白的长查询按 trigram 覆盖率算），计算命中块覆盖了多少比例的查询词（`coverage`），结合 bm25 分数；`coverage` 低于阈值且 bm25 低于下限的命中视为低相关，不返回。阈值写成常量，`SqliteKeywordIndex` 可以传参覆盖。
2. 混合检索的缺口判断：关键词只剩低相关命中、向量也低于阈值时，`gap_state` 为 `zero_hits`（与现有四种缺口状态的语义一致）。
3. 在 IVY 评测语料上校准阈值：`behavior.yaml` 里“已入库来源没有匹配内容”的题判为 `zero_hits`；`law`、`catalog`、`stats` 的 Recall@10 不低于 0.85，且不低于本任务之前的值（在 summary 里列出前后对比）。校准过程和最终取值写进 `docs/adr/0003-keyword-threshold.md`。
4. 新测试覆盖：无关常见词查询被过滤、正常查询不受影响、阈值参数可覆盖、混合检索的 gap 判断；`tests/index`、`tests/eval`、`tests/integration` 必须仍然通过。

# 不做
- 不改向量阈值和 RRF 融合方式。
- 不改评测题。
