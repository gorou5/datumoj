---
id: DATUMOJ-MAPLE-145
title: 相关性门槛收紧单词查询，修正靠无关命中通过的旧断言，前后分数做成可执行对比
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-144"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/index/fts.py", "src/datumoj/rikolto/analysis/tools.py", "tests/regressions/test_audit_144.py", "tests/regressions/test_audit_145.py", "tests/integration/test_tachibana_m3.py", "tests/index/**", "tests/analysis/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "sources/**", "src/datumoj/rikolto/index/hybrid.py", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**"]
protected: ["tests/fixtures/**", "eval/questions/**", "src/datumoj/rikolto/eval/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**", "glossary.yaml"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_145.py
    - cmd: make check T=tests/regressions/test_audit_144.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "交付说明里贴出 test_audit_145 打印的各题库前后分数表"]
  review: {by: other_family, checklist: ["单词库外查询（含 月面補助金 这类只命中一个常见 trigram 的）是否判 zero_hits，且夹具不是靠常见词压低 IDF 才通过", "旧的 スタートアップ PDF 断言是否改成 PDF 里真有的词，而不是靠门槛放水", "前后分数是否经 search_docs 测得、写成测试常量、并注明基线提交", "是否越界"]}
  human: sampled
max_attempts: 4
---
# 目标
补上 MAPLE-144 合并后留下的三个缺口：单词查询门槛是照着一条无关命中调出来的；`search_docs` 层没有可复现的前后分数对比；判断依据取的是当前版本的块而不是被引用的那一版。

# 背景
MAPLE-144（合并提交 cb352ef，基线是合并前的 89e8763）给 `search_docs` 加了关键词支撑过滤。问题：
- `tools.py` 的 `MIN_SINGLE_TERM_COVERAGE = 0.2` 是为了让 `tests/integration/test_tachibana_m3.py` 里 `("pdf", "スタートアップ")` 那条断言继续通过。夹具 PDF（`tests/fixtures/gov_web/pdf/text.pdf`）里没有「スタートアップ」也没有「スタート」，只有「アップ」5 处、「ベンチャー」2 处，所以那条命中本来就是无关的。
- 按 0.2：「月面補助金」拆成 3 个 trigram，只命中「補助金」就是 1/3，会判 `ok` 并返回无关的补助金块；8 个以上 trigram 的长词命中 2 个也能过。
- 144 的测试语料里 23 块有 21 块含「補助金」，FTS5 把它的权重压得很低。真实语料里含「補助金」的块是少数，测试结果不代表真实情况。
- 评测框架调用 `retriever.search`（`src/datumoj/rikolto/eval/framework.py`），不经过 `search_docs`，所以现有门槛测试证明不了 `search_docs` 的过滤没让库内题掉分。144 要求的“前后分数对比”三次都没交上来（写到被 git 忽略的 `results/` 里也等于没交）。
- `_supports_query` 用 `self.chunks.get_chunk(hit.chunk_id)` 取当前版本的块，没带 `hit.citation.snapshot_sha256`。
- `tools.py` 从 `fts.py` 引入了私有函数 `_query_terms`。

任务分支从 `feature/0.4` 拉出，合并回它。
- 测试全部离线，嵌入用假嵌入器；不新增依赖，不改 `pyproject.toml`。
- 不改评测框架、题库、夹具和词表。
- 不写账号、密钥、本机绝对路径。

# 要求
1. 单词（不分词）查询的门槛不能靠“命中任意一个 trigram”过关。做法自选（例如要求连续命中的片段覆盖原词至少一半，或按原词长度要求最少命中数），但：
   - 「月面補助金」「月面基地建設補助金」「宇宙港建設補助金」在一份含「補助金」的块只占少数（不超过 1/5）的测试语料上判 `zero_hits`，`hits == []`；
   - 中文、英文的同类问法（经词表扩展后）同样判 `zero_hits`；
   - 库内的单词查询（例如语料里真有的「補助金」「スタートアップ」）仍命中正确的块。
   去掉 `MIN_SINGLE_TERM_COVERAGE = 0.2` 及其注释里对无关命中的依赖；新常量的注释写清依据。
2. 把 `tests/integration/test_tachibana_m3.py` 里 PDF 那一组的查询改成夹具 PDF 里确实有的词（例如「ベンチャー」，先核对夹具），HTML 那一组不动。不放宽其他断言。
3. 新增 `tests/regressions/test_audit_145.py`：经 `AnalysisTools.search_docs`（不是 `retriever.search`）对 IVY 与 TACHIBANA 各题库（`eval/questions/` 里非 maple_ 开头的）跑一遍，按题库算 hit@k，断言不低于基线：
   - 基线在 89e8763 上用同一段代码测得，写成测试里的常量，注释写明基线提交；
   - 测试结束打印“题库 / 基线 / 现值”表，交付说明里贴这张表；
   - `maple_multilingual.yaml` 只打印前后数值，不断言；下降的题在交付说明里列出（跨语言纯向量召回下降是已知取舍，本任务不修）。
4. `_supports_query` 按 `hit.citation.snapshot_sha256` 取被引用的那一版块。
5. `fts.py` 把 `_query_terms` 改为公开名字（例如 `query_terms`），更新所有调用方。
6. 保留 144 的回归测试并继续通过；`test_audit_144.py` 可以补用例，不能删用例或放宽断言。
7. `make lint` 0 条，全量测试全部通过、要跑完；全量命令排除现存的 1 个 xfail，不改那个测试，不新增 xfail 或 skip。
8. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改 `hybrid.py`、评测框架、题库、夹具、词表。
- 不修跨语言纯向量召回（只量出来）。
- 不连真实网络，不读本机 `data/`。
