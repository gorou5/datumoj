---
id: DATUMOJ-IVY-303
title: 观测值库：DuckDB + Parquet，只读 SQL 与出处回溯
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-002"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/store/observations.py", "tests/store/test_observations.py", "results/DATUMOJ-IVY-303/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/store/test_observations.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-303/summary.md 完成说明"]
  review: {by: human, checklist: ["只读拦截能否被绕过", "空值和注记是否被当成 0", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
所有统计数值存成统一的 Observation，可以用 SQL 只读查询并拿到每行的出处。

# 背景
开发线：C 统计线。PLAN 4.3 第 2 条：数值只能来自 SQL。不同调查的数据不能相加或算增长率。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-303/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 实现 `ObservationStore` 协议：按 `source_id/table_key` 写 Parquet，DuckDB 视图 `observations`；重写同一张表时整体替换。
2. `query_readonly(sql, max_rows=1000, timeout_s=10)`：只允许单条 SELECT/WITH，只读打开；拒绝 INSERT、ATTACH、COPY、PRAGMA、多语句和 `read_csv` 等文件函数；超时和超行数返回清楚的错误。
3. SQL 白名单之外再加一层：查询连接设置 `enable_external_access=false`，然后 `lock_configuration=true`，使查询里无法再把它改回来。测试要包含“用 SET 重新打开外部访问”被拒绝。
4. 结果附带涉及行的去重出处（查询需保留 `obs_id`，或用 `with_provenance` 包装）。
5. `describe()`：列出调查、指标、地区范围、时间范围，给模型看。
6. 继承 `ObservationStoreSuite` 跑合格测试，再补只读拦截的绕过尝试、空值和注记保留。

# 不做
- 不写具体统计来源的转换。
