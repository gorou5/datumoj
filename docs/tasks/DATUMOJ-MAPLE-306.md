---
id: DATUMOJ-MAPLE-306
title: 观测值引用键（D-04）：出处按 (source_id, table_key, obs_id) 关联，不串表，歧义明说
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-002"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/store/observations.py", "src/datumoj/analysis/tools.py", "tests/store/test_observation_refs.py", "tests/regressions/test_audit_306.py", "results/DATUMOJ-MAPLE-306/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/store/test_observation_refs.py
    - cmd: make check T=tests/store
    - cmd: make check T=tests/analysis
    - cmd: make check T=tests/regressions/test_audit_306.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-306/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
两张统计表里出现相同的 `obs_id` 时，`query_sql` 给出的出处指向结果行真正来自的那张表；不能唯一确定时明说，不猜。

# 背景
开发线：C 统计线。审核 D-04：`DuckDBObservationStore._provenance`（`store/observations.py`）对全库按 `obs_id` 查询，再按 source/table 排序 `setdefault` 取第一条，源码注释承认 `obs_id` 只在表内唯一。契约（MAPLE-002）已提供 `ObservationRef`。查询视图 `observations` 由各 Parquet 表合成。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-306/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 结果里带 `source_id`、`table_key`、`obs_id` 三列时，按三元组精确关联出处。
2. 只带 `obs_id` 时：该 `obs_id` 在全库唯一则照常关联；不唯一则这些行不附出处，`warnings` 写明哪些 `obs_id` 有歧义、应在 SELECT 里加上 `source_id`、`table_key`。
3. 工具层（`analysis/tools.py` 的 `query_sql`）返回的每条出处带 `ObservationRef` 的字符串形式；MCP 的 `query_sql` 说明里补一句“需要逐行出处时 SELECT 带上 source_id、table_key、obs_id”。（MCP 文件不在本任务 scope，说明文字写进 summary，由集成任务加。）
4. 新测试 `tests/store/test_observation_refs.py` 覆盖：两表同 `obs_id` 带三列时各自正确、只带 `obs_id` 时歧义告警、唯一时照常、`ObservationRef` 往返。审核回归测试 `tests/regressions/test_audit_306.py`：用合成数据离线复现审核场景——两张表有相同 `obs_id` 时，`query_sql` 结果行的出处指向该行所在的表——断言修好以后的行为，必须通过（不能标 skip / xfail）。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改 Parquet 的存储布局。
- 不做聚合的证据状态（MAPLE-307）。
