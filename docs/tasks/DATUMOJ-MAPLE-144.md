---
id: DATUMOJ-MAPLE-144
title: 相关性门槛覆盖短词退回 LIKE 和仅向量命中，库外问题判 zero_hits
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/index/fts.py", "src/datumoj/rikolto/index/hybrid.py", "src/datumoj/rikolto/analysis/tools.py", "tests/index/**", "tests/analysis/**", "tests/test_search*.py", "tests/regressions/test_audit_144.py", "results/DATUMOJ-MAPLE-144/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/cloud.py", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**", "src/datumoj/rikolto/eval/**", "evals/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_144.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "门槛前后各评测题库的分数对比"]
  review: {by: other_family, checklist: ["短词退回 LIKE 时是否也套用相关性门槛", "只有向量命中、没有关键词支撑时是否仍会把库外问题判 ok", "库内问题（IVY、TACHIBANA 评测题）是否没有因此掉分", "zero_hits 时是否不返回无关结果"]}
  human: sampled
max_attempts: 5
---
# 目标
资料库里没有的问题，`search_docs` 判为 `zero_hits`，不拿无关内容凑数。这是 0.2 目标 5 承诺的相关性门槛在真实语料上的修复。

# 背景
2026-11-07 做 TACHIBANA 真实问答验收（本机真实语料），库外问题都返回了无关结果，`gap_state` 却是 `ok`：
- 「月面基地 建設 補助金」：先给出警告「查询含少于 3 个字的词，已退回 LIKE 扫描」，再返回旧法令第 32 条（关键词命中）和大阪市补助金申请书样式（只有向量命中）。
- 「月面基地建設補助金」（不分词）和英文「lunar base construction subsidy」：同样返回补助金相关的无关段落。
- 库内问题正常，例如「大阪市 AI スタートアップ 支援策」第 1 位就命中大阪市的对应页面。

`fts.py` 的 `min_keyword_coverage` 和 `min_keyword_bm25` 只作用于 FTS 路径；短词走 LIKE 时没有门槛。向量检索总会返回最近的 k 条，没有绝对的距离门槛。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. 短词退回 LIKE 的路径要套用与 FTS 同等含义的覆盖率门槛：查询中多个词时，命中块要覆盖足够多的词，不能只因为某一个短词出现就算命中。
2. 只有向量命中、关键词完全不支撑的结果，不能单独让 `gap_state` 成为 `ok`。用最小的规则实现，例如：要求关键词至少部分支撑，或者向量距离低于一个可配置阈值。阈值写成默认值并说明依据，不新增配置文件。
3. 被门槛滤掉后没有结果时，返回 `zero_hits`，`gap_detail` 写明原因；不返回被滤掉的结果。现有的 `truncated`、`not_ingested`、`not_indexed` 语义不变。
4. 新增 `tests/regressions/test_audit_144.py`，用现有夹具或测试内构造的小语料覆盖：
   - 含短词的库外查询判 `zero_hits`；
   - 只有向量命中的库外查询判 `zero_hits`（用假嵌入器构造）；
   - 含短词的库内查询仍能命中正确的块；
   - 英文库外查询判 `zero_hits`。
5. 不改评测框架、题库和夹具。用现有题库跑 `rikolto eval` 或现有评测测试，IVY 和 TACHIBANA 的门槛测试要全部通过；在证据里给出门槛前后的分数对比。
6. `make lint` 0 条，全量测试全部通过，要跑完。全量命令排除 `test_wisteria_m1.py` 里现存的 1 个 xfail，不要修改那个测试，也不要新增 xfail 或 skip。
7. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改分词和 trigram 索引结构，不重建索引。
- 不改多语言检索的召回（归 0.3 目标 7）。
- 不访问真实网络和本机 `data/`。
