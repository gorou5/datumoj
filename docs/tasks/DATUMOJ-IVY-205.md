---
id: DATUMOJ-IVY-205
title: 修复：没有“条”、正文直接是“项”的法令也能解析
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/xml_law.py", "tests/parsers/test_xml_law_paragraph_only.py", "results/DATUMOJ-IVY-205/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/parsers/test_xml_law.py", "tests/parsers/test_xml_law_roles.py"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_xml_law_paragraph_only.py
    - cmd: make check T=tests/parsers/test_xml_law.py
    - cmd: make check T=tests/parsers/test_xml_law_roles.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-205/summary.md 完成说明"]
  review: {by: other_family, checklist: ["只有“项”的法令是否每项一块、locator 为 para=N", "有“条”的法令解析结果是否和修复前完全一致", "附则、别表的处理是否不受影响", "原有 202/204 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
`MainProvision` 下面直接是 `Paragraph`、没有 `Article` 的法令（例如《失火ノ責任ニ関スル法律》）能正常解析入库，不再报“法令 XML 没有可索引条文”。

# 背景
开发线：B 文档线（修复 DATUMOJ-IVY-202）。M1 真实抓取验收（H3）抓了 200 部法令，其中 **31 部**是这种结构：本则只有一个或几个“项”，没有“条”。多数是明治、大正时期的太政官布告、勅令、省令和条约说明书，也有现行仍常用的法律，例如明治三十二年法律第四十号（失火ノ責任ニ関スル法律）。原有 22 部测试样本都有“条”，所以 202 没遇到过。

已从 H3 的真实抓取补录三份样本到 `tests/fixtures/egov_law/`（见该目录 README 末尾“补录”一节）：
- `law_data.132AC1000000040.asof2026-10-21.xml-in-json.json`：失火责任法，本则 1 项；
- `law_data.135AC1000000050.asof2026-10-21.xml-in-json.json`：年齢計算ニ関スル法律，本则 3 项；
- `law_data.120IO0000000001.asof2026-10-21.xml-in-json.json`：海底電信線保護万国連合条約ノ説明書，本则 4 项。

计划见 `docs/PLAN.md`；测试全部离线，不能改依赖和契约。

# 要求
1. 本则（`MainProvision`）下直接有 `Paragraph` 时，每个“项”一块：locator 为 `para=N`（N 取 `Paragraph@Num`，没有就按顺序从 1 编号），`title_path` 为法令名加“第N項”（只有一项时可只用法令名），正文为该项全文；`chunk_id` 仍是 `<doc_key>#<locator>`。
2. 同一份 `MainProvision` 里“条”和“项”混在一起时，两种都要切出来，不能漏掉任何一部分。
3. 本则和附则都没有任何可索引正文时，才报 `ParseError`。
4. 有“条”的法令解析结果和修复前完全一致；附则、别表处理不变。原有 `tests/parsers/test_xml_law.py`、`tests/parsers/test_xml_law_roles.py` 已列为 protected，必须全部通过。
5. 新测试 `tests/parsers/test_xml_law_paragraph_only.py`：用上面三份真实样本（meta 按 `role=law_data` 构造），断言块数、locator、正文开头文字；再加一个人工构造的“条和项混在本则里”的小 XML。

# 不做
- 不改流水线、采集器、契约。
