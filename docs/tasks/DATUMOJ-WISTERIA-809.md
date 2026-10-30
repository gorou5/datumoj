---
id: DATUMOJ-WISTERIA-809
title: Eltiro 缺口报告：每份需求缺什么、可能的来源，汇总给 Rikolto 来源计划
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-803"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eltiro/gaps.py", "docs/eltiro/gaps.md", "tests/eltiro/test_gaps.py", "tests/regressions/test_audit_wisteria_809.py", "results/DATUMOJ-WISTERIA-809/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "src/datumoj/eltiro/matching.py", "src/datumoj/eltiro/match_store.py", "src/datumoj/eltiro/eval.py", "src/datumoj/eltiro/routing.py", "src/datumoj/eltiro/model_cache.py", "src/datumoj/eltiro/providers.py", "src/datumoj/eltiro/prompts.py", "src/datumoj/eltiro/executor.py", "src/datumoj/eltiro/operations.py", "src/datumoj/eltiro/areas.py", "src/datumoj/eltiro/runs.py", "src/datumoj/eltiro/rematch.py", "src/datumoj/eltiro/alerts.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_809.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-809/summary.md 完成说明（含缺口种类和判定规则、两个样例需求在合成数据上的缺口报告）"]
  review: {by: other_family, checklist: ["缺口种类是否分对", "可能的来源是否只来自注册表、画像和需求自己写的偏好来源，没有臆造", "给使用方的报告是否不提及其他使用方、不含其他需求的内容", "汇总报告是否只给宿主、不写使用方发件目录", "是否只读匹配记录和画像", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
对每份需求在某个代次的匹配结果，列出缺什么、为什么缺、可能从哪里补。把所有在用需求的缺口汇总成一份报告，作为 Rikolto 来源计划的输入。

# 背景
开发线：Eltiro。设计见 `docs/plans/0.4-wisteria.md` 第 4 节第 7 条。已完成的实现：
- 802 的画像库，含三种可得程度：`observations`、`catalog_only`、`document_only`；
- 803 的匹配库 `SQLiteMatchStore`。

需求里的 `min_sources` 规定一个指标至少要几个来源。`preferred_sources` 是需求自己写的偏好来源，可能还没接入注册表（例如 0.4 的国际来源要等 H2）。

执行时产生的缺口由 804 记在它自己的运行结果里，例如倍率未确认、地区码对不上。805 打输出包时会把两边合起来，本任务不读 804 的结果。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务只新建 `gaps.py`。
- `cli.py` 这一轮归 804 改，本任务**不加命令行**，只提供 Python 接口；命令行以后接上。
- 同时在跑的 804、808 各用自己的文件，都不要碰。
- Eltiro 的模型、契约和测试工具只读，匹配和画像只读；Rikolto 的代码一律不改。需要改契约或模型时，写进 `summary.md` 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_wisteria_809.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **缺口种类**：对一份需求的每个指标，按该代次的匹配记录和画像判定：
   - `missing`：没有任何候选；
   - `catalog_only` 或 `document_only`：只有这两种可得程度的候选，没有观测值；
   - `pending_confirmation`：有观测值候选，但都还没确认，也没有自动绑定；
   - `below_min_sources`：确定的绑定（人确认或自动绑定）来自的来源数少于 `min_sources`；
   - `rejected_only`：候选都被人否决了。
   
   一个指标可以同时有多种缺口。
2. **可能的来源**，只从已有信息里找，不臆造：
   - 只在目录或文档里出现的候选的来源；
   - 需求的 `preferred_sources` 里还没在注册表的；
   - 在注册表里、但不在这个使用方 `allowed_sources` 里的来源。这一项只写进给宿主的汇总报告，不写进给使用方的报告。
3. **每份需求的报告**：结构化对象和 JSON，写在 `<data>/eltiro/gaps/<demand_id>-v<version>-g<generation>.json`。报告不含需求原文，不提及其他使用方。
4. **汇总报告**：
   - 把全部在用需求的缺口按来源和指标概念聚合，写成 `<data>/eltiro/reports/gaps-g<generation>.md` 和同名 JSON，给宿主看，作为 Rikolto 来源计划的输入。
   - 汇总报告只给宿主，不写任何使用方的发件目录。
   - 写入都是原子的。
5. **Python 接口**：
   - `gap_report(demand, generation, …)`：一份需求的报告；
   - `aggregate_gaps(generation, …)`：汇总报告；
   - 两者都只读匹配库、画像库和注册表。
6. **回归测试** `test_audit_wisteria_809.py` 覆盖：
   - 五种缺口各自在该出现时出现、不该出现时不出现；
   - `below_min_sources` 按不同来源计数，同一来源的多条序列只算一个；
   - 可能的来源只来自目录候选、需求的偏好来源和注册表；
   - 给使用方的报告里没有其他使用方的 id、需求和授权外来源；
   - 汇总报告不写进任何发件目录；
   - 不调用匹配库、画像库、Rikolto 的写接口；
   - 两个样例需求在合成数据上生成正确的报告。
7. `make check T=tests` 全部通过。

# 不做
- 不加命令行，不做管理页面和每周报告（601）。
- 不读 804 的运行结果，不做输出包（805）。
- 不调用模型，不主动推送。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
