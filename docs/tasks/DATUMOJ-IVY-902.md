---
id: DATUMOJ-IVY-902
title: 检查点 M2 集成：目录、统计、JMA 接入，统计题达标
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-901", "DATUMOJ-IVY-203", "DATUMOJ-IVY-303", "DATUMOJ-IVY-304", "DATUMOJ-IVY-305", "DATUMOJ-IVY-401", "DATUMOJ-IVY-402", "DATUMOJ-IVY-703", "DATUMOJ-IVY-705", "DATUMOJ-IVY-706"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/wiring.py", "src/datumoj/cli.py", "sources/**", "tests/integration/test_stats_e2e.py", "results/DATUMOJ-IVY-902/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_stats_e2e.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-902/summary.md 完成说明"]
  review: {by: human, checklist: ["是否只改了接线", "数值是否与原表一致", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
目录、统计、JMA 三类来源用真实实现跑通，目录题 Recall@10 ≥ 0.85，数值题 100% 正确。

# 背景
开发线：集成。这是 IVY 版本检查点 M2 的集成门槛。只允许改接线和 `sources/`。

本任务合并并通过人工验收后，打 tag `v0.1.0-ivy-m2`。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-902/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 检查各来源的 `sources/*.yaml`（jp-egov-data、jp-stat-dashboard、jp-estat-files、jp-jma）能被注册表加载，并和采集器的 `kind` 对上。
2. 端到端测试：回放 fixture → crawl → parse → index，然后 `datumoj sql` 查都道府县人口，结果与原表一致并带出处。
3. 跑 `catalog.yaml` 和 `stats.yaml`，断言门槛。
4. JMA：回放两次 feed，第二次只新增新条目。
5. （固定要求）集成测试**不能写入仓库里受 git 跟踪的路径**（包括 `results/`）：评测报告等证据默认写到临时目录，只有设置了环境变量 `DATUMOJ_EVIDENCE_DIR` 时才写到该目录（参考 `tests/integration/test_law_e2e.py` 的写法）。否则在 main 上跑全量测试会弄脏工作区，调度器会拒绝合并其他任务。

# 不做
- 不改各开发线的模块。
