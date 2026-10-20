---
id: DATUMOJ-IVY-601
title: 解析与索引流水线（增量，基于插件发现）
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/pipeline.py", "tests/test_pipeline.py", "results/DATUMOJ-IVY-601/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-601/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
从 manifest 找出还没解析的快照，用插件发现选解析器，结果写进 chunk、目录、观测值存储；重复运行不重复处理。

# 背景
开发线：F 入口线。只依赖契约，测试全部用假实现和测试内定义的假解析器，所以不用等任何真实模块。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-601/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. `parse_pending(deps, source_id=None)`：按 `(source_kind, content_type)` 用 `discover_parsers` 选解析器；按 sha256 和解析器版本判断是否已处理。
2. ParseResult 分发：chunks → ChunkStore + KeywordIndex（+ VectorIndex，可选）；observations → ObservationStore；datasets/resources → CatalogStore。
3. 解析失败记录原因并继续；最后汇总。
4. `reindex(source_id=None)`：全量重建索引。
5. 测试：跑两遍第二遍什么都不做、坏快照不影响其他、解析器版本升级触发重解析、没有匹配的解析器时记为 `no_parser`。
6. （契约二对齐）用 `select_parser()` 选解析器，`Manifest.snapshot_meta()` 取出 meta 传给 `parse`；同一文档的快照按抓取时间顺序写入 `ChunkStore.put_document`（最近一次写入即“当前版本”）；解析出资源后调用 `CatalogStore.set_content_state(resource_url, state)` 更新入库程度；解析状态用 `record_parse` / `get_parse`。

# 不做
- 不改 scope 以外的文件。
