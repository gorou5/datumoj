---
id: DATUMOJ-IVY-603
title: 分析工具层：search / catalog / sql / chunk / cite / version / coverage
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/analysis/**", "tests/analysis/test_tools.py", "results/DATUMOJ-IVY-603/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/analysis/test_tools.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-603/summary.md 完成说明"]
  review: {by: human, checklist: ["每个结果是否都带可还原的出处", "来源文本是否都标注为数据", "query_sql 是否仍然只读", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
MCP 和 CLI 共用的一组工具函数，输入输出都是可序列化的 dict，每个结果带出处，来源正文标注为数据。

# 背景
开发线：F 入口线。只依赖协议，测试用假实现，所以和检索线、统计线同时开发。集成任务 DATUMOJ-IVY-903 再接真实实现。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-603/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. `search_docs(queries, k, sources, since)`：调 `Retriever`，返回摘要、出处、`gap_state`、新鲜度。
2. `search_catalog`、`query_sql`（附 `describe()` 说明和出处）、`get_chunk(chunk_id, context=1)`、`cite(snapshot_sha256, locator)`（回到 raw 重新定位，定位不到返回 `not_found`）、`version()`（包版本、git 提交、数据目录、索引构建时间）、`coverage()`（全部来源、状态、新鲜度）。
3. `glossary.yaml` + `expand_terms(query)`：中英政务常用词到日文的对照，约 100 条，不调用 LLM。
4. 来源文本一律放在 `SourceText` 里；说明性字段不能拼接来源文本。
5. 每条出处都带完整的时间口径（`time_basis` 和对应日期）和许可署名；来源是 `internal_only` 时在结果里标注“仅供内部使用”；法令结果带施行基准日，有未施行修改时给出提示。
6. 测试覆盖每个工具的正常、空结果、错误输入。
7. （契约二对齐）工具层只依赖 `datumoj.core.contracts` 的协议，测试用 `datumoj.testing.fakes` 的假实现；`search_catalog` 结果标注用 `ContentState`。

# 不做
- 不调用 LLM，不写 MCP 协议层。
