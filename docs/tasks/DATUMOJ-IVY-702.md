---
id: DATUMOJ-IVY-702
title: 法令评测题 20 道
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-IVY-701"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["eval/questions/law.yaml", "tests/eval/test_questions_law.py", "results/DATUMOJ-IVY-702/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/eval/test_questions_law.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-702/summary.md 完成说明"]
  review: {by: human, checklist: ["问法是否像真实用户", "期望答案是否真的正确", "有没有挑简单题", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
20 道法令检索题，期望答案写到条，作为检索质量的回归基线。

# 背景
开发线：G 评测线。只能用 `tests/fixtures/egov_law/` 里有的法令出题（直接读 XML 原文定答案）。参考交接包 `handoff/digital-assets-20261020/06_后续工作与验收.md` 的问法。是否达标由集成任务 DATUMOJ-IVY-901 在真实流水线上检验。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块（例如 304 用 302 的地区码表）；其他任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-702/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. 至少 12 道日文、4 道中文、4 道英文；中英文题在 `queries` 里给出改写后的日文查询，并保留原文。
2. 期望命中写到条（`doc_key` + `art=`），`notes` 说明为什么。
3. 问法像真实用户，不照抄条文原句。
4. 测试：题库能被 `load_questions` 加载；每个期望的 `doc_key` 对应的法令确实在 fixture 里，期望的条号确实存在（直接在测试里用 lxml 查 XML）。

# 不做
- 不改 `src/`。
