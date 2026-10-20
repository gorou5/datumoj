---
id: DATUMOJ-IVY-501
title: chunk 存储与 FTS5 trigram 关键词索引
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002", "DATUMOJ-IVY-003"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/store/chunks.py", "src/datumoj/index/fts.py", "tests/index/test_fts.py", "results/DATUMOJ-IVY-501/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: /opt/homebrew/bin/uv run --frozen pytest -q tests/index/test_fts.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-501/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
Chunk 存进 SQLite，用日文关键词（不分词）检索到。

# 背景
开发线：E 检索线。PLAN 4.2：SQLite FTS5 trigram。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-501/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 实现 `ChunkStore` 和 `KeywordIndex` 协议：同一 `doc_key` 新快照替换旧块，旧快照记录保留以便引用旧版本。
2. FTS5 `tokenize='trigram'`；索引 `title_path` 和 `text`；SQLite 不支持 trigram 时报错。
3. `search(query, k, source_ids=None) -> list[Hit]`；查询先 `normalize`；两个字以下退回 LIKE 并标注。
4. 继承两套合格测试，再补 30 条以上日文块的命中、排序（标题命中优先）、过滤、替换。

# 不做
- 不做向量。
