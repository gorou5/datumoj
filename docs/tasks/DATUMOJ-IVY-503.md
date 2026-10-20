---
id: DATUMOJ-IVY-503
title: 混合检索（RRF、多查询）与四种缺口状态
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-501", "DATUMOJ-IVY-502", "DATUMOJ-IVY-102", "DATUMOJ-IVY-104"]
runner: codex
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/index/hybrid.py", "src/datumoj/index/coverage.py", "tests/index/test_hybrid.py", "results/DATUMOJ-IVY-503/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_hybrid.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-503/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
实现 `Retriever`：关键词加向量、多个查询一起检索，用 RRF 融合；查不到时准确说明是哪种缺口。

# 背景
开发线：E 检索线。PLAN 4.3 第 5–7 条。旧项目把四种缺口混在一起，用户分不清。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-503/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. `hybrid_search(queries, k, source_ids=None, date_range=None)`：每个查询分别跑 FTS 和向量，RRF（k=60）融合，按 `chunk_id` 去重；记录 `matched_by`。
2. 向量不可用时退回纯关键词，标注 `degraded=true`。
3. `gap_state(source_ids, hits)`：`ok`、`zero_hits`、`not_ingested`（注册表有但没有成功快照）、`fetch_failed`（附原因）、`out_of_scope`（注册表没有）；给出最近的可查内容。
4. 继承 `RetrieverSuite`，再补 RRF 计算、去重、降级、四种缺口各两个用例。

# 不做
- 不改 scope 以外的文件。
