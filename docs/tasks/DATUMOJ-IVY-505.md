---
id: DATUMOJ-IVY-505
title: 修复：只有低相似度向量结果时判为零命中
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/index/hybrid.py", "tests/index/test_hybrid_threshold.py", "results/DATUMOJ-IVY-505/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/index/test_hybrid.py"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_hybrid_threshold.py
    - cmd: make check T=tests/index/test_hybrid.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-505/summary.md 完成说明"]
  review: {by: other_family, checklist: ["关键词命中是否完全不受阈值影响", "只有低于阈值的向量结果时是否判为 zero_hits", "阈值是否可配置、默认值是否写明来由", "原有 503 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
混合检索里，相似度低于阈值的向量结果不再算命中；一个查询只剩这类结果时，缺口状态是 `zero_hits`，而不是 `ok`。

# 背景
开发线：E 检索线（修复 DATUMOJ-IVY-503）。向量检索总能返回“最近的”若干条，哪怕和问题毫不相关。集成任务 DATUMOJ-IVY-903 中，问“木星地表积雪量”这种资料库里不可能有的问题，也因为有向量近邻而被判为 `ok`，缺口题正确率因此只有 7/8。`LanceVectorIndex` 返回的分数是 `1 - distance`（见 `src/datumoj/index/vector.py`）。

计划见 `docs/PLAN.md`；测试全部离线，不能改依赖和契约。

# 要求
1. `HybridRetriever` 增加参数 `min_vector_score: float`（构造时传入，默认值写成模块常量，并在注释和 summary 里写明来由：默认值是临时的，将在 H5 用 bge-m3 和评测集校准）。分数低于它的向量结果在融合前丢弃。
2. 关键词结果不受这个阈值影响。
3. 丢弃后如果没有任何结果，缺口状态按原有规则判定（注册且已入库的来源就是 `zero_hits`），`warnings` 里说明“向量近邻相似度过低，已忽略”。
4. 原有 `tests/index/test_hybrid.py` 已列为 protected，必须全部通过。
5. 新测试 `tests/index/test_hybrid_threshold.py`（用 `datumoj.testing.fakes`，构造带指定分数的向量结果）：只有低分向量结果 → zero_hits；高分向量结果保留；关键词命中加低分向量 → 只剩关键词命中；阈值可配置。

# 不做
- 不改向量索引、嵌入、关键词索引、契约。
