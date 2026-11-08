---
id: DATUMOJ-MAPLE-136
title: 修 Federal Register 更正文档编号和 Eurostat 接口变化，两个来源恢复入库
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/fedreg.py", "src/datumoj/rikolto/crawlers/eurostat.py", "src/datumoj/rikolto/parsers/fedreg.py", "tests/crawlers/test_fedreg.py", "tests/crawlers/test_eurostat.py", "tests/parsers/test_fedreg.py", "tests/parsers/test_eurostat.py", "tests/integration/test_eurostat_e2e.py", "tests/regressions/test_audit_136.py", "results/DATUMOJ-MAPLE-136/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_fedreg.py
    - cmd: make check T=tests/crawlers/test_eurostat.py
    - cmd: make check T=tests/regressions/test_audit_136.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "Federal Register 分多轮完成的测试说明"]
  review: {by: other_family, checklist: ["编号校验放宽后是否仍只接受明确的格式，URL 不能被注入", "分多轮抓取时覆盖进度是否只在真正完整后才推进", "已抓过的文档是否不再重复请求或重复入库", "Eurostat 的 children 响应是否仍能取到 geo、unit 标签"]}
  human: sampled
max_attempts: 5
---
# 目标
`us-fedreg` 和 `eu-eurostat` 能真正抓取入库。这两个来源到现在没有任何数据入库。

# 背景
2026-11-06 用真实响应复现的原因：
- **Federal Register**：`crawlers/fedreg.py` 的 `_children` 用 `\d{4}-\d+` 校验 `document_number`。列表第 1 页 2000 条里有 6 条是更正文档，编号形如 `C1-2026-19812`，抛 ValueError，整轮中止。已实测 `/documents/C1-2026-19812.json` 返回 200，类型是 Notice，有 `full_text_xml_url`。
- **Eurostat**：`/datastructure/ESTAT/<ID>?references=all` 现在返回 400，错误是 `ERR_DSD_REFERENCES: Only processConstraints and None or Children are supported`。已实测 `references=children` 返回 200、含 6 个 Codelist；`none` 不含 codelist。
- **Federal Register 的量**：窗口从 2026-10-01 起，截至 11-05 共 2,470 条，每条要请求详情和 XML，按每秒 1 次约 82 分钟，一轮跑不完。`_covered_until` 只在一次运行完整时才推进。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. 放宽 Federal Register 编号校验，接受普通编号和更正编号（如 `C1-2026-19812`），其他格式仍然拒绝。`parsers/fedreg.py` 如有同样的限制一并修。
2. 列表里单条记录编号无效时，只跳过这一条并记 warning，不中止整轮。列表整体结构无效（缺 count、results 等）仍然报错。
3. Eurostat 改用 `references=children`。确认 `read_structure` 能从这种响应取到 geo、unit 标签。`tests/crawlers/test_eurostat.py` 和 `tests/integration/test_eurostat_e2e.py`（第 49、131 行）里写死的 `references=all` 都要改成 `children`，否则全量测试失败。
4. Federal Register 分多轮必须能追平：
   - 新增测试：模拟每轮只能请求一部分，多轮之后全部文档入库，并且已成功的文档不再重复请求详情和 XML。
   - 如果现有逻辑做不到（比如每轮都从头请求，永远追不上），在 `crawlers/fedreg.py` 内用最小改动修好。覆盖进度仍然只有确认完整时才能推进，不能跳过未抓到的文档。
5. 新增 `tests/regressions/test_audit_136.py` 覆盖 1–4。fixtures 不改，测试数据写在测试里。
6. `make lint` 0 条，全量测试全部通过，要跑完，不要中途中断。全量命令排除 `test_wisteria_m1.py` 里现存的 1 个 xfail（调度器把 xfail 算作失败，与本任务无关）。不要修改那个测试，也不要新增 xfail 或 skip。
7. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进结果说明。

# 不做
- 不改 `sources/*.yaml`、抓取频率和窗口起点。
- 不改 `cloud.py` 和来源判坏规则（任务 138 负责）。
- 不访问真实网络和 R2。
