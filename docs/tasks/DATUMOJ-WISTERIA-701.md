---
id: DATUMOJ-WISTERIA-701
title: Eltiro 评测：试点需求的标准匹配答案、查准查全门槛、输出数值核对、许可闸门反例
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-003"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["eval/eltiro/**", "src/datumoj/eltiro/eval.py", "tests/eltiro/test_eval.py", "tests/regressions/test_audit_wisteria_701.py", "results/DATUMOJ-WISTERIA-701/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/**", "sources/**", "schemas/**", "eval/questions/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_701.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-701/summary.md 完成说明（含每个试点需求的标准答案来源和待 H2 确认的项）"]
  review: {by: human, checklist: ["四个试点需求是否都有需求文件和标准答案", "标准答案是否都写了依据（来源的官方指标代码或表号）、没有臆造", "反例是否覆盖口径相近但不能混用的序列", "查准、查全门槛和计算方法是否写清", "数值核对是否只和来源原值比、容差写明", "许可闸门反例是否覆盖 no_outflow、derived_only 带原文、混合来源", "依赖还没接入的来源的条目是否标为待 H2、不计入门槛", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
给 Eltiro 建评测：四个试点案例的需求文件和**标准匹配答案**、匹配的查准率和查全率门槛、输出数值和来源原值的核对方法、许可闸门的反例；附一个打分模块，供 902–904 检查点使用。

# 背景
开发线：G。试点（`docs/plans/0.4-wisteria.md` 第 6.8 节）：V13 日本每月勤劳统计（e-Stat、ILO、OECD）、#002 一个国家三个 GDP（IMF WEO、OECD、WDI）、V07 希腊赤字修订链（Eurostat、IMF）、#003 贸易镜像（UN COMTRADE、WTO）。#002 和 V07 的需求文件已在 `docs/plans/0.4-wisteria-samples/`（只读，评测里引用或复制）；V13 和 #003 的需求文件由本任务按同样的写法起草。案例背景见 `docs/plans/0.4-wisteria-cases.md` 和 plan.datumoj.com/cases、/verify 的页面（只读参考，不抓取）。

契约见 WISTERIA-001（`DemandSpec`、`MatchRecord`、`OutputManifest`、`Outflow`）。国际来源（IMF、OECD、ILO、UN COMTRADE、WTO）的接入要等 H2 核验，它们的注册表 id 以计划写法为准（`intl-imf-weo`、`intl-oecd` 等），以 H2 定稿为准。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务只新建 `eval/eltiro/` 和 `eltiro/eval.py`；Eltiro 其他文件和 Rikolto 的代码一律不改，Rikolto 的评测题（`eval/questions/`）不动。同时在跑的 801、802 各自用别的文件。
- 回归测试写在 `tests/regressions/test_audit_wisteria_701.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，不访问网络；临时文件一律放 `tmp_path`。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **需求文件**：`eval/eltiro/demands/` 放四个试点的需求（#002、V07 从样例复制并保持一致；V13、#003 新写），都能通过 `load_demand` 校验。
2. **标准答案**：`eval/eltiro/gold/<demand-id>.yaml`：每个指标列出应匹配的序列（来源 id、表或数据集、指标代码等能定位的身份）和**不应匹配的反例**（口径相近但不能混用的序列，如实际 GDP、人均 GDP、PPP、中央政府赤字、预测值）。每条都写依据（来源官方的指标代码、表号或文档链接）。依据不足或来源尚未经 H2 核验的条目标 `status: pending_h2`，不计入门槛。不臆造代码。
3. **门槛**：`eval/eltiro/thresholds.yaml`：确定性档和整体的查准率、查全率门槛（建议起点：查准 ≥ 0.95、查全 ≥ 0.8，写明理由），反例被自动绑定的次数必须为 0。
4. **数值核对**：对每个试点挑若干个可核对的点（国家、年份、指标、来源、来源原值、来源页面），写进 `eval/eltiro/values/`；核对方法：输出值和来源原值比，容差写明（默认完全相等，倍率换算后按有效数字）。原值必须来自来源官方页面或已录的样本，记下核对日期；还没接入的来源标 `pending_h2`。
5. **许可闸门反例**：`eval/eltiro/license_cases.yaml`：至少覆盖 `no_outflow` 来源进输出、`derived_only` 来源带原文或片段、混合来源取最严、缺许可按最严、使用方配置放宽许可，这些都必须被闸门拦下（805 用）。
6. **打分模块** `eltiro/eval.py`：读标准答案和一组 `MatchRecord`，算查准率、查全率、反例绑定数，跳过 `pending_h2`；读数值核对表和一个输出包的数据，报告不一致；读许可反例，对一个给定的闸门函数逐条判定。结果是结构化的报告对象，失败时说清哪一条。
7. **回归测试** `test_audit_wisteria_701.py` 覆盖：四个需求文件都通过校验；#002、V07 和样例一致；标准答案和反例都有依据字段；打分模块在构造的完美、漏配、错配、反例绑定输入上算对；`pending_h2` 不计入；许可反例对一个"全放行"的假闸门全部判失败、对一个"全拒绝"的假闸门全部判通过。
8. `make check T=tests` 全部通过。

# 不做
- 不实现匹配、执行和许可闸门（803–805）。
- 不抓取任何来源，不访问网络；数值原值只用已有样本或人工记录的官方值。
- 不改 Rikolto 的评测题。
