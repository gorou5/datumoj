---
id: DATUMOJ-IVY-502
title: 向量索引：嵌入接口与 LanceDB
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/index/vector.py", "src/datumoj/index/embed.py", "tests/index/test_vector.py", "results/DATUMOJ-IVY-502/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_vector.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-502/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
Chunk 可以批量嵌入并存进 LanceDB 做向量检索；嵌入模型可替换，测试用确定性的假模型。

# 背景
开发线：E 检索线。默认 bge-m3 通过本机 Ollama 运行。真实模型的效果比较由人在主对话里做（H5）。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-502/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 实现 `Embedder`：`OllamaEmbedder`（httpx 调 `/api/embed`，批量、超时、重试）和 `HashEmbedder`（测试用）。
2. 实现 `VectorIndex`：`add`（按 `chunk_id` + 文本哈希只嵌入新的或变了的）、`search`、`delete(doc_key)`；记录模型名和维度，换模型拒绝混用。
3. 中途失败重跑时跳过已完成的块。
4. 测试只用 `HashEmbedder` 和本地假 Ollama 服务。
5. （契约二对齐）`VectorIndex.search` 返回 `IndexResult`；`add` 返回实际嵌入的块数；`chunk_id` 跨快照稳定（`<doc_key>#<locator>`），据此判断“没变就不重嵌”。以 `EmbedderSuite`、`VectorIndexSuite` 为准。

# 不做
- 不改 scope 以外的文件。
