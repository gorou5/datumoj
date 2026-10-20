---
id: DATUMOJ-IVY-901
title: 检查点 M1 集成：法令链路端到端，法令题达标
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-105", "DATUMOJ-IVY-201", "DATUMOJ-IVY-202", "DATUMOJ-IVY-501", "DATUMOJ-IVY-602", "DATUMOJ-IVY-702"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/wiring.py", "src/datumoj/cli.py", "sources/jp-egov-law.yaml", "tests/integration/test_law_e2e.py", "results/DATUMOJ-IVY-901/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_law_e2e.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-901/summary.md 完成说明"]
  review: {by: human, checklist: ["是否只改了接线", "端到端测试是否真的走了真实实现", "门槛未达是否如实报告", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
法令从抓取（回放）到检索整条链路用真实实现跑通，20 道法令题在 fixture 语料上 Recall@10 ≥ 0.85。

# 背景
开发线：集成。这是 IVY 版本检查点 M1 的集成门槛。只允许改接线（`wiring.py`、`cli.py`、`sources/jp-egov-law.yaml`），各模块有 bug 时写进 summary，由人决定开修复任务，不在这里顺手改。

本任务合并并通过人工验收后，打 tag `v0.1.0-ivy-m1`。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-901/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 端到端测试：ReplayTransport 回放 `tests/fixtures/egov_law/` → `datumoj crawl jp-egov-law` → `parse` → `index` → `search`，断言能检索到指定条文并带完整出处。
2. 第二次 crawl 全部是未变化或跳过，不重复下载。
3. 在同一语料上用 DATUMOJ-IVY-701 的框架跑 `eval/questions/law.yaml`，断言 Recall@10 ≥ 0.85；报告写到 `results/DATUMOJ-IVY-901/`。
4. 达不到门槛就如实报告失败的题，不改检索代码。

# 不做
- 不改各开发线的模块。
