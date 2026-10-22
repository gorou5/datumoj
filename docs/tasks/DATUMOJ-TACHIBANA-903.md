---
id: DATUMOJ-TACHIBANA-903
title: 检查点 m3 集成：监控、变化报告、全部评测门槛、MCP 实机问答（TACHIBANA 完成）
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-902", "DATUMOJ-TACHIBANA-102", "DATUMOJ-TACHIBANA-201", "DATUMOJ-TACHIBANA-202", "DATUMOJ-TACHIBANA-205", "DATUMOJ-TACHIBANA-301", "DATUMOJ-TACHIBANA-401", "DATUMOJ-TACHIBANA-402", "DATUMOJ-TACHIBANA-403", "DATUMOJ-TACHIBANA-501", "DATUMOJ-TACHIBANA-601", "DATUMOJ-TACHIBANA-602", "DATUMOJ-TACHIBANA-603", "DATUMOJ-TACHIBANA-701", "DATUMOJ-TACHIBANA-702"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/wiring.py", "src/datumoj/cli.py", "src/datumoj/eval/framework.py", "eval/questions/changes.yaml", "tests/integration/test_tachibana_m3.py", "tests/eval/test_framework_changes.py", "results/DATUMOJ-TACHIBANA-903/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "eval/questions/law.yaml", "eval/questions/catalog.yaml", "eval/questions/stats.yaml", "eval/questions/behavior.yaml", "eval/questions/gov_web.yaml", "eval/questions/tachibana_gaps.yaml"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_tachibana_m3.py
    - cmd: make check T=tests/eval/test_framework_changes.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-903/summary.md 完成说明"]
  review: {by: human, checklist: ["是否只改了接线、评测框架的 change 题型和题目", "门槛是否全部达标", "变化报告是否与事件一致", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
TACHIBANA 的全部能力一起达标：监控提醒、每周变化报告、IVY 和 TACHIBANA 的全部评测题、MCP 的新工具。

# 背景
开发线：集成。TACHIBANA 检查点 m3 和版本完成的门槛。变化题需要评测框架支持 `list_changes`，在本任务里加。人工验收（H4）在 Claude Code 里注册 MCP 做真实问答，记到 `docs/acceptance/v0.2.0-tachibana.md`。

本任务合并并通过人工验收后，打 tag `v0.2.0-tachibana`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-903/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 评测框架加 `kind: change` 题型：用 `list_changes` 取结果，期望写事件类型、来源和 doc_key 前缀；指标为命中率。`eval/questions/changes.yaml` 5 题（新增、修改、删除、抓取失败、零变化各 1），语料由测试里两轮回放（第二轮修改、删除部分样本）生成。
2. 端到端：两轮回放政策网页和统计样本 → 生成变化事件 → `datumoj report weekly` 生成报告，报告各节与事件一致；人为制造连续失败和 JMA 断档时，`schedule status` 出现对应提醒。
3. 门槛：IVY 的 60 题和 TACHIBANA 的新题（701 的 20 题 + 本任务 5 题）全部按 PLAN 第 10 节的门槛达标（Recall@10 ≥ 0.85、数值精确 100%、引用可还原率 ≥ 0.95、缺口正确拒答 100%）；变化题命中率 100%。
4. MCP：通过 stdio 启动服务器，`list_changes` 和带 `kinds` 的 `search_docs` 能正常调用并返回出处。
5. （固定要求）集成测试**不能写入仓库里受 git 跟踪的路径**（包括 `results/`）：评测报告等证据默认写到临时目录，只有设置了环境变量 `DATUMOJ_EVIDENCE_DIR` 时才写到该目录（参考 `tests/integration/test_law_e2e.py` 的写法）。
6. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改各开发线的模块（评测框架的 change 题型除外）。
