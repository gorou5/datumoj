---
id: DATUMOJ-IVY-204
title: 修复：法令解析器只处理现行版全文快照，列表和修订历史跳过
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/xml_law.py", "tests/parsers/test_xml_law_roles.py", "results/DATUMOJ-IVY-204/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/parsers/test_xml_law.py"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_xml_law_roles.py
    - cmd: make check T=tests/parsers/test_xml_law.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-204/summary.md 完成说明"]
  review: {by: other_family, checklist: ["列表和修订历史快照是否被跳过且不算失败", "现行版全文的解析结果是否和修复前完全一致", "原有 202 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
法令来源的列表页（`role=law_list`）和修订历史（`role=law_revisions`）快照不再被当成法令正文解析而报错；只有现行版全文（`role=law_data`）才解析。

# 背景
开发线：B 文档线（修复 DATUMOJ-IVY-202）。集成任务 DATUMOJ-IVY-901 用真实实现跑通法令链路时发现：`LawXMLParser` 声明 `content_type = "*"`，流水线按“来源类型 + 内容类型”选解析器，所以 e-Gov 法令来源的**所有** JSON 响应都交给了它，列表页报 `meta 缺少合法的 law_id`，22 份修订历史报 `法令输入不是有效的 XML 或 e-Gov JSON 响应`，`datumoj parse` 因此退出 1。

采集器（201）写进 `FetchTask.meta` 的 `role` 有三种：`law_list`、`law_revisions`、`law_data`（见 `src/datumoj/crawlers/egov_law.py`）。计划见 `docs/PLAN.md`；测试全部离线，不能改依赖。

# 要求
1. `parse(snapshot, data, meta)`：`meta["role"]` 是 `law_list` 或 `law_revisions` 时，返回**空的** `ParseResult`（没有 documents/chunks），`warnings` 里写一句“非正文快照（role=…），跳过”，**不抛异常**，流水线会把它记为成功而不是失败。
2. `meta` 缺失、或没有 `role` 键时，保持原行为（按正文解析，坏输入照旧抛 `ParseError`），以兼容直接用 XML 调用的场景。
3. `role=law_data` 的解析结果必须和修复前完全一致（原有 `tests/parsers/test_xml_law.py` 已列为 protected，必须全部通过）。
4. 新测试 `tests/parsers/test_xml_law_roles.py`：用 `tests/fixtures/egov_law/` 里真实的列表页和修订历史样本，加上对应 role 的 meta，断言返回空结果加警告；再用一份 law_data 样本断言照常解析。

# 不做
- 不改流水线、不改契约、不改采集器。
