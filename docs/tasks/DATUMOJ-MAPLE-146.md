---
id: DATUMOJ-MAPLE-146
title: 相关性门槛按词的少见程度（IDF）加权，语料里没有的关键词不能被常见词顶替
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-145"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/index/fts.py", "src/datumoj/rikolto/analysis/tools.py", "tests/regressions/test_audit_144.py", "tests/regressions/test_audit_145.py", "tests/regressions/test_audit_146.py", "tests/index/**", "tests/analysis/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "sources/**", "src/datumoj/rikolto/index/hybrid.py", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**"]
protected: ["tests/fixtures/**", "eval/questions/**", "src/datumoj/rikolto/eval/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**", "glossary.yaml"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_146.py
    - cmd: make check T="tests/regressions/test_audit_144.py tests/regressions/test_audit_145.py"
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "交付说明里贴出 test_audit_145 打印的各题库前后分数表（含 maple_multilingual）"]
  review: {by: other_family, checklist: ["分词和英文的库外问题是否判 zero_hits，且不是靠改写查询绕开", "英文大阪问题是否命中正确的 PDF", "权重是否来自索引里的真实文档频率，而不是写死的词表", "库内题和多语言题是否没有掉分", "是否越界"]}
  human: sampled
max_attempts: 4
---
# 目标
`search_docs` 的关键词支撑门槛改成按词的少见程度加权。语料里一次都没出现、或者极少出现的词，是问题的核心；常见词（建設、補助金、base、subsidy）命中再多，也不能顶替它。

# 背景
MAPLE-144、145 合并后（feature/0.4，dfdfb65），在本机真实语料（约 1 万文档、2.7 万块）上重测 0.2 的 Q7：
- 「月面基地建設補助金」「月面補助金」「月球基地建设补贴」：已无命中 ✅
- 「月面基地 建設 補助金」（分词）：`ok`，命中 e-Gov 法令 `211IO0000000194` 第 32 条（港湾埋立，含「建設」「補助金」）❌。按词数，3 个词命中 2 个，过了 0.5。
- 「lunar base construction subsidy」：`ok`，命中大阪吸引外国金融企业的英文 PDF 第 12 页（含 base、Subsidy）❌。4 个词命中 2 个，也过了 0.5。
- 「Osaka City support startups advanced financial markets subsidy」：库内问题，正确答案就是上面那份 PDF，现在却没有命中 ❌。8 个词没够一半。

根本原因：`tools.py` 的 `_supports_query` 和 `fts.py` 的 `keyword_coverage` 按词数算覆盖率，每个词同等重要。「月面基地」「lunar」在语料里出现 0 次，本该一票否决，现在只算作没命中的一个词。

任务分支从 `feature/0.4` 拉出，合并回它。
- 测试全部离线，嵌入用假嵌入器；不新增依赖，不改 `pyproject.toml`。
- 不改评测框架、题库、夹具和词表。
- 不写账号、密钥、本机绝对路径。

# 要求
1. 覆盖率改为按 IDF 加权：每个查询词的权重随它在索引里的文档频率（或块频率）降低而升高；权重从关键词索引里实时取（例如 FTS5 的 `fts5vocab`，或对索引做计数查询），不写死词表。语料里出现 0 次的词拿最高权重。门槛数值自定，常量注释写清依据。
   - 词表扩展后的同义词按 145 的做法算同一个概念，频率取整个概念的频率。
   - 单词（不分词）查询沿用 145 的连续片段规则，可以叠加 IDF，但不能放松。
   - 不在 scope 里的调用方（例如评测框架）行为不变。
2. 新增 `tests/regressions/test_audit_146.py`，用一份按真实情况造的内存语料（几十块，常见词要真的常见），复刻上面三个失败：
   - 一块类似港湾埋立的法令条文，含「建設」「補助金」；
   - 一块类似大阪外资金融激励的英文 PDF 页，含 Osaka、City、support、startups、financial、markets、Subsidy、Base；
   - 若干其他块，让「補助金」「建設」、support、subsidy 成为常见词。
   断言：
   - 「月面基地 建設 補助金」「lunar base construction subsidy」「月球基地 建设 补贴」在开、关向量两种情况下都判 `zero_hits`，`hits == []`；
   - 「Osaka City support startups advanced financial markets subsidy」第 1 位命中那份英文 PDF 页；
   - 144、145 的回归用例全部照旧通过。
3. `test_audit_145.py` 的基线断言照旧通过（IVY、TACHIBANA 各题库不低于 89e8763 的基线）。另外给 `maple_multilingual.yaml` 加一条断言：不低于 145 合并后（dfdfb65）的值，这个值写成常量并注明提交。
4. `make lint` 0 条，全量测试全部通过、要跑完；全量命令排除现存的 1 个 xfail，不改那个测试，不新增 xfail 或 skip。
5. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改 `hybrid.py`、评测框架、题库、夹具、词表。
- 不处理检索变慢（本机真实语料上单条查询 2–5 分钟，另行排查）。
- 不连真实网络，不读本机 `data/`。
