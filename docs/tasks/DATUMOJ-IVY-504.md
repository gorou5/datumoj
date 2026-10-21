---
id: DATUMOJ-IVY-504
title: 修复：关键词检索按词拆分，不再把整条查询当一个短语
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/index/fts.py", "tests/index/test_fts_multiword.py", "results/DATUMOJ-IVY-504/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/index/test_fts.py"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_fts_multiword.py
    - cmd: make check T=tests/index/test_fts.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-504/summary.md 完成说明"]
  review: {by: other_family, checklist: ["多词查询能否召回只包含部分词、且词不相邻的块", "排序是否让包含更多查询词的块靠前", "FTS 查询串是否做了转义，用户输入不能注入 FTS 语法", "原有 501 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
`SqliteKeywordIndex.search` 能处理空格分隔的多词查询，例如 `個人情報 第三者提供 本人の同意`：包含这些词（不必相邻、不必全部出现）的块都能被召回，包含的词越多排得越靠前。

# 背景
开发线：E 检索线（修复 DATUMOJ-IVY-501）。集成任务 DATUMOJ-IVY-901 在 22 部真实法令上跑 20 道法令题，Recall@10 = 0.000。原因：`src/datumoj/index/fts.py` 第 123 行把规范化后的**整条查询**加引号作为一个 FTS 短语（`'"' + q + '"'`），而题库的查询都是空格分隔的多个词，法令正文里没有这样的连续整句，所以全部零命中。

计划见 `docs/PLAN.md`；测试全部离线，不能改依赖。

# 要求
1. 查询先 `normalize`，再按空白（半角和全角空格）拆成词；空查询报 `ValueError`（保持原约定）。
2. 三个字以上的词各自作为 FTS trigram 短语，用 `OR` 组合；每个词都要转义（双引号加倍），用户输入不能注入 FTS 语法（`AND`、`NEAR`、`*`、`:` 等都当普通文字）。
3. 一两个字的短词不进 FTS（trigram 搜不到），按原有 LIKE 回退的方式参与（有上限和超时），结果和 FTS 结果合并；只有短词时就是原来的 LIKE 行为。
4. 排序：先按命中的不同查询词个数降序，再按原有的 BM25 加权分数（标题加权保留）。
5. 单个词的查询行为和修复前一致（原有 `tests/index/test_fts.py` 已列为 protected，必须全部通过）。
6. 新测试 `tests/index/test_fts_multiword.py`：用人工构造的十几个日文块覆盖——多词不相邻也能召回；命中词多的排前；全角空格分隔；含短词的混合查询；带 `"`、`AND`、`*` 的查询不报错也不被当成语法；`source_ids` 过滤仍然有效。

# 不做
- 不改向量检索、混合检索（503）、契约。
- 不做日文分词。
