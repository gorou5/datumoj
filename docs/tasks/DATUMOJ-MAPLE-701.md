---
id: DATUMOJ-MAPLE-701
title: 评测题：欧美 12、日本新来源 8、多语言与跨语言 6、跨国比较 4，离线在新来源样本上跑
project: DATUMOJ
status: doing
priority: 2
depends_on: ["DATUMOJ-MAPLE-201", "DATUMOJ-MAPLE-302", "DATUMOJ-MAPLE-303", "DATUMOJ-MAPLE-304", "DATUMOJ-MAPLE-305", "DATUMOJ-MAPLE-402", "DATUMOJ-MAPLE-403", "DATUMOJ-MAPLE-501"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["eval/questions/maple_west.yaml", "eval/questions/maple_jp.yaml", "eval/questions/maple_multilingual.yaml", "eval/questions/maple_compare.yaml", "tests/integration/test_maple_eval.py", "src/datumoj/rikolto/eval/**", "results/DATUMOJ-MAPLE-701/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**", "sources/**"]
protected: ["tests/fixtures/**", "tests/regressions/**", "eval/questions/behavior.yaml", "eval/questions/catalog.yaml", "eval/questions/changes.yaml", "eval/questions/gov_web.yaml", "eval/questions/law.yaml", "eval/questions/stats.yaml", "eval/questions/tachibana_gaps.yaml"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_maple_eval.py
    - cmd: make check T=tests/integration/test_full_eval.py
    - cmd: make check T=tests/eval
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-701/summary.md 完成说明（各类结果和失败题清单）"]
  review: {by: other_family, checklist: ["每道题的答案能否在样本里核实", "跨国比较题的预期回答是否不换算", "失败题是否如实记录、没有改题迁就", "已有题和已有评测是否原样通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
给 MAPLE 的新来源补 30 道评测题，离线在样本语料上跑，和 IVY、TACHIBANA 的题一起作为 m3 的验收依据。

# 背景
计划 `docs/plans/0.3-maple.md` 第 3 节目标 8、第 7 节 701、702（回答规则：跨国比较不自行换算单位、年份、货币；多语言出处；数据截止时间写代次时间）。
- 题目格式照 `eval/questions/*.yaml` 已有的写法和 `datumoj.rikolto.eval` 的题目模型；已有题不改。
- 新来源：201 Federal Register、302 Eurostat、303 WDI、304 KSJ + 经济普查、305 NDB + ABR、402 data.gouv.fr、403 data.europa.eu；e-Stat API（301）还在等 appId，不出它的题（以后由 301 补）。
- 本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。测试全部离线；不改契约和采集、解析代码（发现缺陷写进 summary，不在本任务修）。

# 要求
1. **题目**（共 30 道，答案都必须能从 `tests/fixtures/` 的样本里得到）：
   - 欧美 12（`maple_west.yaml`）：Federal Register、Eurostat、WDI、data.gouv.fr、data.europa.eu 每个至少 2 道；
   - 日本新来源 8（`maple_jp.yaml`）：KSJ、经济普查、NDB、ABR 各 2 道，NDB 至少 1 道问到遮蔽格子（应答“遮蔽，不能给数值”）；
   - 多语言与跨语言 6（`maple_multilingual.yaml`）：英语问英语资料、法语问法语资料、日语或中文问英文 / 法文资料各至少 1 道；
   - 跨国比较 4（`maple_compare.yaml`）：至少 1 道 Eurostat 与 WDI 口径不同（欧元 vs 美元、1 月 1 日人口 vs 年中人口），预期回答是分别给出、不换算；
   - 每道题写预期命中（块、数据集或观测值出处）和缺口状态；另加至少 3 道应判 `zero_hits` 的题。
2. **`tests/integration/test_maple_eval.py`**：用这些样本离线建库（抓取用录制样本），跑全部 30 + 题，输出各类 Recall@10 和缺口状态准确率；门槛：各类 Recall@10 ≥ 0.8，缺口状态准确率 1.0。达不到时如实记下哪道题、原因（检索问题还是采集 / 解析问题），不改题目去迁就结果。
3. 已有的 `tests/integration/test_full_eval.py` 和 `tests/eval` 原样通过。
4. `summary.md` 写每类的结果和失败题清单；`make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不改已有题目。
- 不做真实问答（那是 H5）。
- 不出 e-Stat API 的题。

# 重做要求（第 1 轮，2026-10-31）
第 1 次尝试的题目和 `test_maple_eval.py` 都通过了（各类 Recall@10 1.000 / 0.875 / 1.000 / 1.000，缺口准确率 1.000），只是全量测试里 `tests/integration/test_tachibana_m3.py::test_all_ivy_and_tachibana_questions_pass` 失败：`load_questions` 会递归读入新题库，而 m3 的语料里没有新来源。这是任务文件没写到，已在 `feature/0.4` 上把那个测试改为只评原有 7 个题库（提交 19c9cc9，CLI 那一段补在之后的提交里），本任务不需要、也不许改它。本轮从 `feature/0.4` 重新开始，其余要求不变；第 1 次尝试在 summary 里记下的 NDB 表头漏检、WDI 假库精度问题照样写进 summary，不在本任务修。

# 重做要求（第 2 轮，2026-10-31）
上一轮第 1 次尝试的题目和 `test_maple_eval.py` 全部达标，只剩 m3 测试后半段的 `rikolto eval --gate` 仍读整个题库目录而失败。这一段也已在 `feature/0.4` 上改好（用 `--questions` 只给原有 7 个题库），本任务仍不改 m3 测试。本轮从 `feature/0.4` 重新开始；分类门槛（各类 Recall@10 ≥ 0.8、缺口准确率 1.0）是硬要求。
