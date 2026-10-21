---
id: DATUMOJ-IVY-607
title: 修复：向量嵌入失败不能让解析失败；可用环境变量关闭向量
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/pipeline.py", "src/datumoj/wiring.py", "src/datumoj/cli.py", "tests/test_pipeline_vectors.py", "results/DATUMOJ-IVY-607/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_pipeline.py", "tests/test_pipeline_retry.py", "tests/test_cli.py"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline_vectors.py
    - cmd: make check T=tests/test_pipeline.py
    - cmd: make check T=tests/test_pipeline_retry.py
    - cmd: make check T=tests/test_cli.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-607/summary.md 完成说明"]
  review: {by: other_family, checklist: ["嵌入失败时文档和关键词索引是否照常入库、解析记为成功", "向量缺失是否被记录并能补做，而不是静默丢失", "DATUMOJ_VECTORS=off 是否真的不创建也不调用向量索引", "原有流水线和 CLI 测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
向量嵌入不可用时（Ollama 没装、没有模型、连接失败），解析和关键词索引照常完成，向量只是暂缺并被记录下来，之后可以补做；另外可以用环境变量直接关闭向量。

# 背景
开发线：F 入口线（修复 DATUMOJ-IVY-601/602）。M1 真实抓取验收（H3）时，这台机器装了 Ollama 但还没拉 bge-m3，`OllamaEmbedder` 返回 404，`src/datumoj/pipeline.py` 的 `_dispatch` 在 `deps.vectors.add(chunks)` 处抛出 `EmbeddingError`，整份快照被记成解析失败：200 部法令里有 169 部因此没有入库，连关键词检索也用不了。PLAN 4.2 和 503 的设计是“向量不可用时退回纯关键词检索”，入库这一侧也应该一样。

计划见 `docs/PLAN.md`；测试全部离线（用 `datumoj.testing.fakes` 和会抛错的假 Embedder），不能改依赖和契约。

# 要求
1. `_dispatch` 里：先写文档、块和关键词索引；向量写入失败（`EmbeddingError`、连接错误等）时不抛出，这份快照仍记为解析成功，在解析汇总里另列“向量暂缺”的快照及原因。
2. 暂缺的向量要能补做：提供 `reindex` 的向量补做路径（例如 `datumoj index --vectors-only`，或 `index --rebuild` 时重试向量），补做成功后不再列为暂缺。实现方式自定，写进 summary。
3. 环境变量 `DATUMOJ_VECTORS=off` 时，`wiring.build` 不创建向量索引（`vectors=None`），也不连接 Ollama；检索自动走纯关键词模式（503 已支持）。
4. `datumoj status` 增加一行向量索引状态：关闭、可用（块数）、或暂缺（暂缺块数和最近一次错误）。
5. 原有 `tests/test_pipeline.py`、`tests/test_pipeline_retry.py`、`tests/test_cli.py` 已列为 protected，必须全部通过。
6. 新测试 `tests/test_pipeline_vectors.py`：嵌入抛错时文档和关键词照常入库且记为成功；暂缺列表正确；补做后清空；`DATUMOJ_VECTORS=off` 时 wiring 不创建向量索引；status 输出包含向量状态。

# 不做
- 不改向量索引和嵌入模块本身（502）、不改契约。
