---
id: DATUMOJ-MAPLE-147
title: 相关性门槛：关键词式查询里语料没出现的词占权重三分之一以上时否决
project: DATUMOJ
status: cancelled
priority: 1
depends_on: ["DATUMOJ-MAPLE-146"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/index/fts.py", "src/datumoj/rikolto/analysis/tools.py", "tests/regressions/test_audit_144.py", "tests/regressions/test_audit_145.py", "tests/regressions/test_audit_146.py", "tests/regressions/test_audit_147.py", "tests/index/**", "tests/analysis/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "sources/**", "src/datumoj/rikolto/index/hybrid.py", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**"]
protected: ["tests/fixtures/**", "eval/questions/**", "src/datumoj/rikolto/eval/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**", "glossary.yaml"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_147.py
    - cmd: make check T="tests/regressions/test_audit_144.py tests/regressions/test_audit_145.py tests/regressions/test_audit_146.py"
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "交付说明里贴出 test_audit_145 打印的各题库前后分数表（含 maple_multilingual）"]
  review: {by: other_family, checklist: ["测试语料的词频比例是否照真实语料（见背景表），而不是让常见词几乎每块都有", "语料里出现 0 次的词是否一票否决，且只看原始查询、不被扩展词顶替", "英文大阪问题和多语言题是否没有掉分", "是否越界"]}
  human: sampled
max_attempts: 6
---
# 目标
多词查询里，在整个索引里一次都没出现的词（同义词合成一个概念后）如果占整条查询权重的 1/3 以上，这条查询就不能被任何块支撑，判 `zero_hits`。问题的核心词资料库里根本没有，就不该拿其他常见词凑出答案；但个别生僻的修饰词缺失不能拖垮整条查询。

# 背景
MAPLE-146（合并提交 5c42112）把门槛改成按 IDF 加权，阈值 0.4。在本机真实语料（27,088 块）上重测 Q7：

| 查询 | 146 之后 | 应为 |
|---|---|---|
| 月面基地 建設 補助金 | ok，命中 e-Gov 法令 211IO0000000194 art=32 | zero_hits |
| lunar base construction subsidy | ok，命中大阪外资金融 PDF page=12 | zero_hits |
| 月面基地建設補助金、月面補助金、月球基地建设补贴、月球基地 建设 补贴 | 无命中 | ✅ |
| Osaka City support startups advanced financial markets subsidy | ok，第 1 位是那份 PDF page=12 | ✅（146 修好的） |
| 另外 4 条日文、中文库内问题 | ok，命中正确 | ✅ |

真实语料上的块频率和 146 的权重（`1+log((N+1)/(df+1))`）：

| 词 | 块频率 | 权重 |
|---|---|---|
| 月面基地 | 0 | 11.21 |
| 建設 | 218 | 5.82 |
| 補助金 / 助成金 | 929 | 4.37 |
| lunar | 0 | 11.21 |
| base | 59 | 7.11 |
| construction | 0（trigram 匹配） | 11.21 |
| subsidy | 4 | 9.60 |

「建設」「補助金」在真实语料里并不常见，加权后 (5.82+4.37)/21.40 = 0.476，超过 0.4。英文那条靠 base、subsidy 拿到约 0.43。146 的测试语料让常见词几乎每块都有，所以压不出这种情况。结论：只调阈值修不好，少见但存在的词和根本不存在的词需要分开处理。

前两次尝试按旧要求“任一词频率为 0 就否决”实现，都在同样 8 个测试上失败：
- 不分词的整句问题（`eval/questions/behavior.yaml`，例如「気象庁資料に木星の地表積雪量は記録されていますか？」）整句算一个词，索引里自然没有，被否决，behavior 题库从 12/12 掉到 2/12；
- 146 的大阪测试（`test_audit_146.py::test_rare_osaka_concepts_rank_the_correct_pdf_first`）语料里没有 advanced，整条被否决；
- 连带 `test_full_eval.py::test_mcp_factory_session_restores_original`、`test_law_e2e.py::test_cli_replay_to_search_with_restorable_citation`、`test_audit_145.py` 的两个用例失败。
旧要求本身有问题，这次按下面改写后的要求做；这些测试一个都不能改。

第 3、4 次尝试按“2 个及以上概念、缺失权重占 1/3 以上就否决”实现，仍然失败：`test_audit_145.py::test_search_docs_scores_against_pre_144_baseline` 里 behavior 题库 12/12 → 5/12。被否决的是自然语言问句（behavior-cross-source-population-law-en/zh、behavior-injection-citizen-notice-en/zh-window、behavior-time-jma-akita-missing-valid-time、behavior-time-jma-okinawa-zh、behavior-time-law-asof、law-lsa-annual-leave）：英文问句里 summarize、who 这类虚词在语料里出现 0 次；日文、中文问句被标点切成十来个字的片段，片段整体也是 0 次。按词数或片段长度划界都会误伤（例如「入社して半年たちました。有給って何日もらえますか？」切成两段，各 11–12 字）。
所以否决只针对关键词式查询：Q7 的两条都是 2–4 个短词、没有任何标点。

按真实语料算，新规则下：「月面基地 建設 補助金」缺失权重 11.21/21.40 = 0.52、「lunar base construction subsidy」缺失 (11.21+11.21)/39.13 = 0.57，都否决；大阪那条最多缺一个词，远低于 1/3。

任务分支从 `feature/0.4` 拉出，合并回它。
- 测试全部离线，嵌入用假嵌入器；不新增依赖，不改 `pyproject.toml`。
- 不改评测框架、题库、夹具和词表。
- 不写账号、密钥、本机绝对路径。

# 要求
1. 在 `_supports_query` 的加权判定前加一条：原始查询（不是词表扩展出的子查询）是关键词式查询时，把块频率为 0 的概念的权重加起来，占全部概念权重之和不低于 1/3（常量，注释写依据），这条原始查询判为不支撑。频率和权重沿用 146 的 `concept_weights`（同义词合并为一个概念，取并集）。
   - 关键词式查询的定义：`query_terms` 拆出 2–4 个概念，且原始查询文本里没有句子标点（全角、半角的 。．.？?！!，,、；;：:）。用常量或小函数表达，注释写依据。
   - 不是关键词式的查询（单个词、不分词的整句、带标点的问句、5 个及以上概念）不适用这条，仍按 145、146 的规则判。
   - 已知局限写进注释：关键词后面加个问号就不适用否决，这是为了不误伤自然语言问句的取舍。
   - 多条原始查询（`queries` 传列表）时，按条判，任一条被支撑即可，和现在一样。
   - 中文、英文查询经词表替换后的“翻译版”也按同一规则判；原文版或翻译版任一被支撑即可，和现在一样。
   - 不支持 `concept_weights` 的索引实现，行为不变。
   - 146 的 IDF 加权和阈值保留，不放松。
2. 新增 `tests/regressions/test_audit_147.py`，语料按背景表的比例造（至少 600 块）：「建設」约 1%、「補助金」约 3–4%、base 约 0.2%、subsidy 只有几块，月面基地、lunar 为 0；其中包括那条港湾埋立法令条文和那份大阪英文 PDF 页。断言：
   - 「月面基地 建設 補助金」「lunar base construction subsidy」在开、关向量两种情况下都判 `zero_hits`，`hits == []`；
   - 「Osaka City support startups advanced financial markets subsidy」第 1 位命中大阪 PDF 页；
   - 先用 146 的判定（不加否决）跑一遍，断言前两条在这份语料上会判 `ok`，证明语料确实复刻了真实失败；
   - 一条 8 个词、只有 1 个生僻修饰词语料里没有的库内查询，仍命中正确的块（防止误伤）；
   - 一条不分词的整句库内问题仍命中正确的块；
   - 一条带句子标点、含若干语料里没有的虚词的英文库内问句（例如 “Summarize who can apply and the application window in this notice.” 的写法），仍命中正确的块；
   - 一条被标点切成两段的日文库内问句仍命中正确的块。
3. 144、145、146 的回归测试照旧通过，不改这些测试文件里已有的用例和断言；`test_audit_145.py` 的基线断言（IVY、TACHIBANA 各题库，maple_multilingual）照旧通过，交付说明贴分数表，列出任何下降的题。
4. `make lint` 0 条，全量测试全部通过、要跑完；全量命令排除现存的 1 个 xfail，不改那个测试，不新增 xfail 或 skip。
5. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改 `hybrid.py`、评测框架、题库、夹具、词表。
- 不处理检索变慢。
- 不连真实网络，不读本机 `data/`。
