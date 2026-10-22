---
id: DATUMOJ-TACHIBANA-702
title: 回答规则更新：政策问答、断言级别与关系断言、目录与统计的取证、变化问答
project: DATUMOJ
status: todo
priority: 2
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["skills/datumoj/SKILL.md", "tests/test_skill_rules.py", "results/DATUMOJ-TACHIBANA-702/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/eval/test_questions_behavior.py", "tests/test_mcp_server.py"]
acceptance:
  automated:
    - cmd: make check T=tests/test_skill_rules.py
    - cmd: make check T=tests/eval/test_questions_behavior.py
    - cmd: make check T=tests/test_mcp_server.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-702/summary.md 完成说明"]
  review: {by: human, checklist: ["规则是否与工具的真实参数和输出一致", "是否保留 IVY 原有规则", "是否覆盖断言级别和关系断言", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
宿主模型（Claude）在回答政策、目录、统计和变化类问题时，知道该怎么取证、怎么标注断言级别、哪些判断不能做。

# 背景
开发线：G 评测线。回答规则在 `skills/datumoj/SKILL.md`（IVY-704 写的）。团队规范新增断言级别和关系断言（`docs/PLAN.md` 第 4.3 节第 8 条）。本版本新增政策网页、PDF、目录快照引用、`list_changes`、`search_docs` 的 `kinds` 参数。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-702/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 加“断言级别”规则：结论分 `proven`（引用原文直接证明）、`inferred`（多条证据推导，写出依据）、`unverified`（待验证），回答里标注；关系类结论只说来源文件载明的关系，不外推股权、控制、合作等关系。
2. 加“政策网页与 PDF”规则：先 `search_docs`（可用 `kinds` 限定 `html_page` / `pdf`），引用到节或页；页面没有日期时说明“无数据日期”，不能说成“现行”；不判断补助、支援措施是否适用于提问者，只说明条件原文和窗口；`needs_ocr` 的 PDF 要说明没能读到正文。
3. 加“目录与统计的取证”规则：数据集先用 `search_catalog`，出处里的快照引用用 `cite` 核对；e-Stat 表先在目录里找到再用 `query_sql` 取数，不同调查的数值不相加、不算增长率。
4. 加“变化问答”规则：用 `list_changes`，说明时间范围；没有变化时说没有，不推测原因。
5. 保持 IVY 已有规则不变（不删、不弱化）。新测试 `tests/test_skill_rules.py` 检查上述规则的关键句都在、IVY 原有的关键规则仍在；读取 SKILL.md 的现有测试（`tests/eval/test_questions_behavior.py`、`tests/test_mcp_server.py`）必须仍然通过。

# 不做
- 不改工具实现。
