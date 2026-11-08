---
id: DATUMOJ-MAPLE-128
title: 按来源关闭向量嵌入（scope.embed 设为 false）：不再产生待补、清掉已有积压、补嵌跳过；jp-egov-law 先关
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-127"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/pipeline.py", "src/datumoj/rikolto/cloud.py", "sources/jp-egov-law.yaml", "tests/test_pipeline_vectors.py", "tests/test_pipeline_retry.py", "tests/test_cloud_run.py", "tests/test_cloud_embed.py", "tests/integration/test_full_eval.py", "tests/regressions/test_audit_128.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-128/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/**", ".env*", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline_vectors.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_cloud_embed.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-128/summary.md 完成说明"]
  review: {by: other_family, checklist: ["embed: false 的来源解析、分块、关键词索引是否照常，只是不写向量、不记待补", "该来源已有的待补记录是否被清掉，vector_pending 和代次的 pending_chunks 是否随之下降", "该来源已经写入的向量是否保留不动", "补嵌（cloud run 的补嵌和 cloud embed）是否跳过该来源", "embed 缺省或为 true 时行为是否与改前完全相同", "embed 不是布尔值时是否明确报错，而不是悄悄当成 true", "是否改了 core、索引实现、爬虫或越界"]}
  human: sampled
max_attempts: 5
---
# 目标
一个来源可以在配置里关掉向量嵌入，只做关键词检索。`jp-egov-law` 先关掉，让 Actions 上的嵌入能追平其他来源的积压。

# 背景
2026-11-03：
- `jp-egov-law`（e-Gov 法令全文，约 9,000 部，目前抓到约 1,700 部）每轮 Cloud run 新增约 4 万个文本块。
- `cloud-embed` 每次约 2 小时，只能嵌入 8,000 到 10,000 个（Actions CPU 上 bge-m3 约每秒 1.4 个）。积压从 4.8 万涨到了 8.7 万。
- 人决定：大来源先不嵌入，只做关键词检索；以后有了更快的机器再回头补。

依赖 127（到期来源排序）。两者都改 `cloud.py`，先合并 127，避免冲突。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- `SourceSpec.scope` 是自由的 JSON 字典（`core/contracts.py`），新增键不用改契约。`core/`、`testing/`、`index/`、爬虫和解析器都不改。
- 测试全部离线，沿用现有夹具和 HashEmbedder。不新增依赖。仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **配置**：来源的 `scope.embed` 是可选布尔值，缺省为 true。不是布尔值时，在第一次用到它的地方抛清楚的错误，不能悄悄当成 true。在 `sources/jp-egov-law.yaml` 的 `scope` 里加上 `embed: false`。
2. **解析发布**（`pipeline.py`）：`embed` 为 false 的来源，解析、分块、关键词索引、观测值和发布状态全部照常，只是：
   - 不调用向量写入；
   - 不记向量待补；
   - 不消耗本轮的嵌入额度。
3. **清掉已有积压**：向量状态里，属于 `embed: false` 来源的待补记录要删掉，在加载或下一次运行时做，实现自定，在 summary 里写明。删掉后 `vector_pending` 和代次的 `pending_chunks` 会下降。
   - 判断记录属于哪个来源，沿用现有的写法（`chunks_for(key)[0].source_id`，或快照的 `source_id`）。
   - 判断不了的记录保留不动。
4. **已有向量保留**：该来源已经写入的向量**不删**。在 summary 里说明这对检索的影响：部分条文能被向量命中，其余只能用关键词。
5. **补嵌跳过**：`cloud run` 的补嵌、`cloud embed`、`_retry_vectors`、`_recover_staged` 都跳过 `embed: false` 的来源。
6. **测试**：
   - `tests/test_pipeline_vectors.py`：
     - `embed: false` 的来源解析后，关键词能搜到，向量没有新增，待补为空；
     - 原本有它的待补记录时，运行后记录被清掉、其他来源的记录保留；
     - `embed` 不是布尔值时报错；
     - 缺省时行为与改前相同。
   - `tests/test_cloud_run.py`、`tests/test_cloud_embed.py`：两种补嵌都跳过该来源，`vector_pending` 不含它的块数。
   - 新增 `tests/regressions/test_audit_128.py`：断言 `sources/jp-egov-law.yaml` 的 `scope.embed` 为 false，其余来源都没有设成 false（改动只限这一个来源）。
   - `tests/integration/test_full_eval.py` 的 `test_real_dependencies_and_complete_corpus` 第 277 行：它用真实的 `sources/jp-egov-law.yaml`，断言法令检索命中里有 `MatchedBy.vector`。关掉 `jp-egov-law` 的嵌入后，这是有意的新行为。只把这一处改成：同一查询仍有命中，命中来自关键词（`MatchedBy.keyword` 或现有的等价标记），并且不含 `MatchedBy.vector`。如果该测试别处还有向量相关断言，同样只改到与新行为一致。在 summary 里写明理由。
   - 其他已有的测试和断言全部不变。
7. `docs/deploy/runners.md` 补一段：`scope.embed` 的含义、关掉之后检索有什么区别，以及以后要重新打开时怎么补（打开后，下一次解析或 reindex 会把它的文档重新记为待补）。打开后的补法先读 `reindex` 的现有行为再写，不要写没有实现的流程。
8. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 打回记录
- 第 1、2 次：全量验收失败在 `tests/integration/test_full_eval.py::test_real_dependencies_and_complete_corpus`（第 277 行，期望法令检索有向量命中）。原因是要求 1 关掉了 `jp-egov-law` 的嵌入。这个文件当时不在 scope 里，现在已加入，按要求 6 改。

# 不做
- 不删已有向量，不改嵌入模型和嵌入器，不改工作流。
- 不改到期排序（127）。
- 除 `jp-egov-law` 外，不改其他来源的配置。
