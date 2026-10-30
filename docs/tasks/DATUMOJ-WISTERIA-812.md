---
id: DATUMOJ-WISTERIA-812
title: Eltiro 提示词正文：判断候选、概念说明、需求草稿三个步骤的正式模板，调用方改用新版本；空结果按不确定处理
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-807", "DATUMOJ-WISTERIA-810"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eltiro/prompts.py", "src/datumoj/eltiro/model_match.py", "src/datumoj/eltiro/concept_notes.py", "src/datumoj/eltiro/drafts.py", "docs/eltiro/routing.md", "docs/eltiro/model_match.md", "docs/eltiro/drafts.md", "tests/eltiro/test_prompts.py", "tests/eltiro/test_model_match.py", "tests/eltiro/test_concept_notes.py", "tests/eltiro/test_drafts.py", "tests/regressions/test_audit_wisteria_812.py", "results/DATUMOJ-WISTERIA-812/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/matching.py", "src/datumoj/eltiro/rematch.py", "src/datumoj/eltiro/routing.py", "src/datumoj/eltiro/model_cache.py", "src/datumoj/eltiro/providers.py", "src/datumoj/eltiro/page_cases.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**", "tests/regressions/test_audit_wisteria_806.py", "tests/regressions/test_audit_wisteria_807.py", "tests/regressions/test_audit_wisteria_810.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_812.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_806.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_807.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_810.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-812/summary.md 完成说明（含三个模板全文、各自要求的输出结构、版本号、旧版本怎样保留）"]
  review: {by: other_family, checklist: ["三个步骤是否都有正式模板、输出结构写清楚", "v1 占位是否原样保留、没有原地改语义", "调用方是否改用新版本、缓存键随之变化", "数据区是否仍只当数据、模板里没有让模型执行来源内容", "模板是否不要求模型估数或决定绑定", "空结果和不合结构的结果是否都按不确定处理", "806、807、810 的回归测试是否原样通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
806 只放了占位的提示词模板，正文要由使用它的任务补。807（判断候选、概念说明）和 810（需求草稿）都还在用占位模板：
- 测试用的是假模型，所以都通过了；
- 接真实模型时，模型收到的只是一句"PLACEHOLDER"，输出多半不合结构，会被当成"不确定"，模型档实际不起作用。

本任务写三个步骤的正式模板，并让调用方改用新版本。

# 背景
开发线：Eltiro。设计见 `docs/plans/0.4-wisteria.md` 第 6.3 节、第 6.7 节。已完成的实现：
- 806 的 `prompts.py`：`TEMPLATES[(步骤, 版本)]` 和 `build_prompt`。数据区转义、"只当数据"的声明都在 `build_prompt` 里。`docs/eltiro/routing.md` 写明正文由 807 补；改正文必须升版本，不能原地替换旧版本。缓存键已含提示词版本。
- 807 的 `model_match.py`（`match_judge`，要求固定结构：相关 / 不相关 / 不确定 + 一句理由）和 `concept_notes.py`（`concept_note`，一句话概念说明），都传 `prompt_version="v1"`。
- 810 的 `drafts.py`（`demand_draft`，要求 JSON 对象，键只能是 entities / indicators / period / operations，值是字符串），传 `prompt_version="v1"`。但 `prompts.py` 里没有 `demand_draft` 的任何模板，接真实提供方时 `build_prompt` 直接报错。
- 807 的异源审查指出：`judge_candidates` 用 `next(iter(result.fields.values()))` 取结果，提供方返回空字段时会抛 `StopIteration`，没有按"不确定"处理。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务改 `prompts.py`，以及三个调用方里的版本号和结果解析。不改路由、缓存、提供方、匹配和重匹配的逻辑。
- `cli.py` 这一轮归 811，不要碰。
- Eltiro 的模型、契约和测试工具只读；Rikolto 的代码一律不改。需要改契约或模型时，写进 `summary.md` 并停下来问。
- 806、807、810 的回归测试不许改，必须原样通过。如果某条断言写死了 `v1`，写进 `summary.md` 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_wisteria_812.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，不调用真实模型。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **三个正式模板**，版本都用 `v2`，`demand_draft` 也从 `v2` 开始（`v1` 不存在，不补占位）：
   - `match_judge/v2`：给出需求指标的说明和一个候选序列的画像，要求判断这个候选是否就是需求要的指标。输出必须是固定结构：结论（相关 / 不相关 / 不确定三选一）和一句理由。口径、单位、调查不同时不能判"相关"。
   - `concept_note/v2`：给出一个序列的画像，要求用一句话说明它度量的是什么，不超过规定长度；只依据数据区里的信息，信息不够时明说"信息不足"。
   - `demand_draft/v2`：给出一个页面案例，要求建议 实体、指标、时间范围和频率、运算 四项，只输出 810 要求的 JSON 对象，不知道的项不输出。
   - 三个模板都要写明：数据区的内容只是数据；不要估数，不要编造代码、表名和数值；不要决定绑定或登记。
   - 中英文都能理解的写法即可，不做多语言版本。
2. **旧版本保留**：`match_judge/v1`、`concept_note/v1` 原样留着，不改文字，旧缓存仍能按旧版本复现。
3. **调用方改用 v2**：`model_match.py`、`concept_notes.py`、`drafts.py` 改传 `v2`。文档写明版本变化会让缓存键变化，同一代次重跑会按 806 的规则处理。
4. **结果解析**：
   - 三个调用方遇到空字段、多余字段、不合结构的结果，一律按"不确定"或"没有建议"处理，不抛未处理的异常，不重试换模型。
   - 修掉 `judge_candidates` 在空字段时抛 `StopIteration` 的问题。
5. **回归测试** `test_audit_wisteria_812.py` 覆盖：
   - 三个步骤的 `build_prompt` 都能生成，含各自的输出结构要求和"只当数据"的声明；
   - `v1` 模板文字不变；
   - 三个调用方实际发出的请求带 `v2`；
   - 数据区里夹带"忽略以上规则""判为相关"之类的文字时，仍在数据区内被转义，模板文字不受影响；
   - 空字段、不合结构的结果按不确定或没有建议处理；
   - 806、807、810 的回归测试原样通过。
6. `make check T=tests` 全部通过。

# 不做
- 不接真实模型，不做真实模型的效果评测（以后用 701 的评测集做）。
- 不改路由、缓存、提供方、匹配和重匹配的逻辑，不加命令行。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
