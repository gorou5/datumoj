---
id: DATUMOJ-WISTERIA-003
title: 模型路由契约：步骤与难度、提供方、许可与预算约束、缓存键与重跑语义
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-102"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/testing/**", "schemas/eltiro/**", "docs/adr/0006-eltiro-boundary.md", "tests/eltiro/**", "tests/regressions/test_audit_wisteria_003.py", "results/DATUMOJ-WISTERIA-003/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example", "docs/PLAN.md", "docs/plans/**", "sources/**", "src/datumoj/rikolto/**", "schemas/*.schema.json"]
protected: ["tests/fixtures/**", "docs/plans/0.4-wisteria-samples/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_003.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-003/summary.md 完成说明"]
  review: {by: human, checklist: ["no_outflow 的数据是否在任何配置下都进不了云端模型", "cloud_model_ok=false 的来源是否只能走本地或 none", "混合来源是否先按许可拆开，拆不开就进人工队列、不调用模型", "路由是否在运行前选定、运行中（含重试）不换", "缓存键是否包含全部八项，使用方之间是否隔离，授权/代次/策略变了是否不复用", "配置里是否只有环境变量名、没有凭证值", "是否只定契约没有调用真实模型", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
定下 Eltiro 的**模型路由契约**：一份配置把"步骤 × 难度"映射到提供方；运行开始前按使用方允许的档位、来源许可和预算选定路由，不满足就降档，降到 `none` 就进人工队列；模型输出按固定的缓存键缓存，同一代次重跑复用原路由和缓存。用模型 + JSON Schema + 接口协议 + 假实现和契约测试表达，不调用任何真实模型。

# 背景
开发线：契约。设计见 `docs/plans/0.4-wisteria.md` 第 6.7 节（下面的要求已经把它写全）。用到的现有契约：
- WISTERIA-001：`DemandSpec` 里的 `ModelBudget`（`max_tier`、`allow_cloud`、`max_calls_per_run`）、`ModelTier`（`none` / `low` / `medium` / `high`）、`ModelUse`（提供方、模型、版本、档位）；
- WISTERIA-002：`GenerationToken`；
- WISTERIA-102：`LicenseInfo.outflow`、`LicenseInfo.cloud_model_ok`（注册表是唯一权威）。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 只改 Eltiro 的契约（`eltiro/models.py`、`eltiro/contracts.py`、`eltiro/testing/`）；Rikolto 的代码一律不动，需要改时写进 `summary.md` 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_wisteria_003.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，不调用任何模型或网络，临时文件一律放 `tmp_path`。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **提供方**：`none`、`claude_plan`（Claude 套餐，经 Claude Code 无头调用）、`ollama`（本地）、`cloud_api`。`claude_plan` 和 `cloud_api` 都算**云端**。
2. **路由配置** `RoutingPolicy`：步骤（至少 `match_judge`、`concept_note`，其他留扩展）× 难度（`low` / `medium` / `high`）→ 提供方和模型；凭证只写**环境变量名**，不能有值（模型校验拒绝看起来像密钥的字段值）；配置有内容哈希，用于缓存键和运行记录。拒绝未知字段。
3. **许可约束**（硬规则，任何配置都绕不过）：
   - `no_outflow` 来源的数据不进云端模型，即使 `cloud_model_ok=true`；
   - `cloud_model_ok=false` 的来源只能走 `ollama` 或 `none`；
   - 需求的 `allow_cloud=false` 时不走云端；档位不超过需求的 `max_tier`；
   - 许可只从注册表的 `LicenseInfo` 取，使用方配置只能再收紧。
4. **混合来源**：一个模型调用的输入涉及多个来源时，先按许可拆成可分别路由的部分；拆不开就进人工队列，不调用模型。
5. **运行前选定**：`RouteDecision` 在运行开始前算出，记录选定的提供方、模型及版本、路由策略哈希、是否降档及原因；约束不满足就逐级降档，降到 `none` 就进人工队列。运行中（包括重试）不换路由；选定的提供方不可用就让这次运行失败，不临时换别的。预算（`max_calls_per_run`）用完后余下的调用进人工队列。
6. **缓存键** `ModelCacheKey` 由八项组成：使用方、授权来源范围、`generation_token`、输入哈希、提供方、模型及版本、路由策略哈希、提示词版本。读缓存前重新校验许可和授权，对不上就不复用。同一代次重跑复用原路由和缓存，缓存缺了就失败，不重新调用。不同使用方的缓存互相隔离。
7. **输出标注**：每个模型产生的字段记录是哪个提供方、模型和档位产生的（沿用 `ModelUse`）。
8. **接口协议**：路由器（输入需求预算、使用方配置、来源许可、路由配置 → 输出 `RouteDecision` 或人工队列项）、模型提供方（只定调用签名）、缓存。`eltiro/testing/` 放内存假实现（假提供方返回固定结果并记录调用）和契约测试套件。
   **许可硬规则必须写进契约测试套件 `ModelRouterSuite`**（2026-10-27 第一次尝试被打回的原因）：要求 3、4、5 的规则——`no_outflow` 不进任何云端提供方（含 `cloud_model_ok=true`）、`cloud_model_ok=false` 只走 `ollama` 或 `none`、`allow_cloud=false` 和 `max_tier` 生效、混合来源拆分或进人工队列、降档到 `none` 进人工队列、提供方不可用时运行失败而不换、预算用完进人工队列——都要作为 `ModelRouterSuite` 里的用例，以 `make_router` 夹具注入路由器实现，不能只在假实现或回归测试里测。806 的真实路由器继承这个套件就自动受这些规则约束。假实现要通过整个套件；`tests/eltiro/` 里用假实现跑一遍套件。回归测试可以调用套件或保留自己的断言，但不能替代套件。
9. **Schema 和 ADR**：新模型导出到 `schemas/eltiro/`，重新导出与仓库一致；在 ADR 0006 里加一节"模型路由"，写清上面的硬规则、降档顺序和缓存键。
10. **回归测试** `test_audit_wisteria_003.py` 必须覆盖：`no_outflow` 不进云端（含 `cloud_model_ok=true` 的情况）；`cloud_model_ok=false` 只走本地或 none；混合来源拆分或进人工队列；`allow_cloud=false` 和 `max_tier` 生效；降档到 none 进人工队列；运行中提供方不可用时失败而不换；不同使用方缓存隔离；授权、代次或路由策略变了不复用旧缓存；重跑缓存缺失时失败；配置里写凭证值被拒绝。
11. `make check T=tests` 全部通过。

# 不做
- 不实现真实的提供方适配（Claude Code 调用、Ollama、云端 API）和预算计费（806）。
- 不核对 Anthropic 条款（H0）。
- 不改 Rikolto 的任何代码和来源配置。
