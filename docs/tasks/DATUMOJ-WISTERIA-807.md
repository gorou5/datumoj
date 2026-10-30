---
id: DATUMOJ-WISTERIA-807
title: Eltiro 模型辅助匹配：第三档模型判断候选、一句话概念说明；全部标注模型生成，不自动绑定
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-803", "DATUMOJ-WISTERIA-806", "DATUMOJ-WISTERIA-808"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/eltiro/model_match.py", "src/datumoj/eltiro/concept_notes.py", "src/datumoj/eltiro/matching.py", "src/datumoj/eltiro/rematch.py", "docs/eltiro/model_match.md", "docs/eltiro/matching.md", "tests/eltiro/test_model_match.py", "tests/eltiro/test_concept_notes.py", "tests/eltiro/test_matching.py", "tests/eltiro/test_rematch.py", "tests/regressions/test_audit_wisteria_807.py", "results/DATUMOJ-WISTERIA-807/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "src/datumoj/eltiro/match_store.py", "src/datumoj/eltiro/eval.py", "src/datumoj/eltiro/routing.py", "src/datumoj/eltiro/model_cache.py", "src/datumoj/eltiro/providers.py", "src/datumoj/eltiro/prompts.py", "src/datumoj/eltiro/executor.py", "src/datumoj/eltiro/operations.py", "src/datumoj/eltiro/areas.py", "src/datumoj/eltiro/runs.py", "src/datumoj/eltiro/alerts.py", "src/datumoj/eltiro/gaps.py", "src/datumoj/eltiro/package.py", "src/datumoj/eltiro/license_gate.py", "src/datumoj/eltiro/drafts.py", "src/datumoj/eltiro/page_cases.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**", "tests/regressions/test_audit_wisteria_803.py", "tests/regressions/test_audit_wisteria_806.py", "tests/regressions/test_audit_wisteria_808.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_807.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_803.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_806.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_808.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-807/summary.md 完成说明（含模型档在三档中的位置、送给模型的输入怎样按许可拆分、模型结论怎样记录、默认配置下行为不变的证明）"]
  review: {by: other_family, checklist: ["模型档是否只对已有候选做判断、不凭空产生序列", "模型结论是否永不自动绑定、不覆盖人的决定、不绕过口径保护", "模型生成的理由和概念说明是否都标注模型生成", "调用是否全部经过 806 的路由、许可拆分、预算和缓存，no_outflow 不进云端", "默认配置（全部 none）下匹配结果是否和 803 / 808 逐条一致", "增量重匹配开模型档时是否仍和全量一致", "提示词里是否不含需求以外使用方的内容", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
给匹配加上第三档"模型判断"，并能为序列生成一句话概念说明。两者都可选、都按 806 的路由走；不配模型时行为和现在完全一样。

# 背景
开发线：Eltiro。设计见 `docs/plans/0.4-wisteria.md` 第 6.2 节（概念说明标注模型生成）、第 6.3 节（三档，每档可关）、第 6.7 节（模型路由）。已完成的实现：
- 803 / 808 的 `match_demand`（确定性档、向量档、人工决定沿用、口径保护）和 `refresh`（增量结果必须和全量一致）；
- 806 的 `SQLiteModelRouter`（`plan`、`invoke`，按许可拆分、降档、预算、人工队列）、`SQLiteModelCache`、三个提供方适配器、`build_prompt`；路由步骤里已有 `match_judge` 和 `concept_note`；
- 001 的 `MatchRecord.tier` 已有 `"model"`，`SeriesProfile.model_generated_fields`，`ModelUse`。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务新建 `model_match.py`（模型判断档）和 `concept_notes.py`（概念说明及其存储），并修改 `matching.py`、`rematch.py` 接入模型档。
- 806 的路由、缓存、提供方和提示词只调用、不改；802 的画像生成不改（概念说明另存，不写回画像库）。
- `cli.py` 本轮不归本任务，**不加命令行**，只提供 Python 接口；命令行由 811 接上。同时在跑的 805、810 用各自的文件，都不要碰。
- Eltiro 的模型、契约和测试工具只读；Rikolto 的代码一律不改。需要改契约或模型时，写进 `summary.md` 并停下来问。
- 803、806、808 的回归测试不许改，必须原样通过。
- 存储用标准库 `sqlite3`，不加依赖。
- 回归测试写在 `tests/regressions/test_audit_wisteria_807.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，不调用真实模型：用记录调用的假提供方；嵌入用 `HashEmbedder`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **模型判断档**（`match_judge`）：
   - 只对前两档已产生的候选做判断，不产生新的序列；没有候选的指标不调用模型。
   - 每次判断的输入：需求里这个指标的说明和正反例、候选序列的画像（概念、单位、口径、地区、时间、来源）。不放其他使用方或其他需求的内容。
   - 难度按候选情况确定（例如分数接近、口径信息缺失时更高），规则写进文档。
   - 调用一律经过 806 的 `plan` / `invoke`：使用方允许的档位、需求预算、来源许可（`no_outflow` 不进云端、`cloud_model_ok=false` 只能本地或 `none`、混合来源拆分或进人工队列）、缓存键都按 806。降到 `none` 时这条候选保持原状，进人工确认队列。
   - 模型输出只接受固定结构（例如 相关 / 不相关 / 不确定 + 一句理由），不合结构的当作"不确定"，不重试换模型。
2. **模型结论怎样记录**：
   - 写成 `tier="model"` 的匹配记录，理由标明"模型生成"，并记下提供方、模型及版本、档位（`ModelUse` 的形状）。
   - **永不自动绑定**：模型认为相关的候选也只进人工确认队列；只能调整候选的置信度和排序。
   - 不覆盖人的确认或否决；人否决过的序列不送模型。
   - 口径保护不变：概念相同但口径、单位、调查不同的候选，模型说相关也不能合并。
3. **概念说明**（`concept_note`）：
   - 只给缺少来源自带说明的画像生成一句话概念说明，存进 `<data>/eltiro/concept_notes.sqlite`，按 代次、序列身份、画像内容哈希 和 806 的缓存键记录。
   - 每条都标注模型生成和提供方、模型及版本；不写回画像库，不当作来源说明使用。
   - 匹配的确定性档不使用概念说明；只作为模型判断档的输入和人工确认时的参考。
   - 同样受许可和预算约束，`none` 时不生成。
4. **接入匹配**：
   - `match_demand` 增加打开模型档的参数（默认关闭）和路由所需的依赖（router、cache、当前许可）；每档都可单独关闭。
   - 默认配置下结果和现在逐条一致。
   - `refresh` 透传同样的设置；开模型档时增量结果仍和全量一致（依赖缓存复现同一结论；缓存缺失时按 806 的重跑规则失败，不偷偷换模型）。
5. **Python 接口**：`judge_candidates(...)`、`generate_concept_notes(generation, ...)`、`list_concept_notes(...)`，以及 `match_demand` / `refresh` 的新参数。
6. **回归测试** `test_audit_wisteria_807.py` 覆盖：
   - 默认配置下 803、808 的结果逐条不变；
   - 模型只对已有候选判断，没有候选时不调用；
   - 模型说相关也不自动绑定；人否决的不送模型；口径不同的不合并；
   - `no_outflow` 来源的内容不进云端提供方；混合来源拆分或进人工队列；
   - 模型输出不合结构时当作不确定；
   - 记录和概念说明都标注模型生成、带提供方和模型版本；
   - 提示词里没有其他使用方和其他需求的内容；
   - 开模型档时增量重匹配和全量一致；缓存缺失时重跑失败；
   - 预算用完时降档进人工队列。
7. `make check T=tests` 全部通过，803、806、808 的回归测试原样通过。

# 不做
- 不加命令行（811），不做管理页面（601）。
- 不让模型估数，不让模型产生新序列，不用模型决定绑定。
- 不改 806 的路由、缓存、提供方和提示词，不改画像生成。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
