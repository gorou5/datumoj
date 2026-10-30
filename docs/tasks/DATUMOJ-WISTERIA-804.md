---
id: DATUMOJ-WISTERIA-804
title: Eltiro 执行器：固定代次取数、地区与时间对齐、运算库、显式换算、运行命令
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-301", "DATUMOJ-WISTERIA-307", "DATUMOJ-WISTERIA-308", "DATUMOJ-WISTERIA-803"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/eltiro/executor.py", "src/datumoj/eltiro/operations.py", "src/datumoj/eltiro/areas.py", "src/datumoj/eltiro/runs.py", "src/datumoj/eltiro/cli.py", "docs/eltiro/executor.md", "tests/eltiro/test_executor.py", "tests/eltiro/test_operations.py", "tests/eltiro/test_areas.py", "tests/eltiro/test_runs.py", "tests/eltiro/test_cli.py", "tests/regressions/test_audit_wisteria_804.py", "results/DATUMOJ-WISTERIA-804/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "src/datumoj/eltiro/matching.py", "src/datumoj/eltiro/match_store.py", "src/datumoj/eltiro/eval.py", "src/datumoj/eltiro/routing.py", "src/datumoj/eltiro/model_cache.py", "src/datumoj/eltiro/providers.py", "src/datumoj/eltiro/prompts.py", "src/datumoj/eltiro/alerts.py", "src/datumoj/eltiro/rematch.py", "src/datumoj/eltiro/gaps.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_804.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-804/summary.md 完成说明（含每个运算的定义和缺值处理、地区码映射的来源、revisions=all 时怎样枚举历次发布、用两个样例需求在合成数据上跑出的结果）"]
  review: {by: human, checklist: ["每次运行是否只固定一个代次、每次查询都带并核对同一个 token", "token 无效或代次不可用时是否停止、不回退最新、不留下结果", "数值是否只从 SQL 取、全程用 Decimal、缺值不补零", "是否只用人确认或自动绑定的匹配，候选不参与计算", "每行结果是否带全部输入的完整身份和运算链", "换算是否只在倍率有来源依据时做、依据是否记录", "单位不同时是否按 require_same_unit 拒绝对齐并报出", "地区码对不上时是否不对齐、记为缺口，没有猜", "运行结果是否只写在 Eltiro 数据目录、不写使用方发件目录", "是否只读 Rikolto、不写它的库", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
让一份已登记的需求能跑起来：
1. 固定一个已发布的数据代次；
2. 生成画像、做匹配（复用 802、803）；
3. 按绑定的序列只用 SQL 取数；
4. 对齐地区和时间，按需求里的运算算出结果；
5. 每一行结果都带完整出处和运算链。

结果写成 Eltiro 内部的运行结果，交给 805 打成输出包。本任务不写使用方的发件目录。

# 背景
开发线：Eltiro。设计见 `docs/plans/0.4-wisteria.md` 第 6.5 节和 ADR 0006、0007。用到的契约和已完成的实现：
- 001 的 `DemandSpec`（指标、实体、时间、对齐、运算）、`MatchRecord`、`InputObservation`、`CitationEntry`；
- 002 / 301 / 307 的固定代次读取：`pin_generation`、`query_readonly(generation_token=…, as_of_release=…)`、`row_evidence`；
- 308 的同一代次只读视图 `open_generation_views` 和历次发布枚举 `list_releases`（`as_of_release` 可传枚举项）；
- 802 的 `generate_profiles` 和 `SQLiteProfileStore`；
- 803 的 `match_demand` 和 `SQLiteMatchStore`。803 要求调用方把三个 Rikolto 视图固定到同一代次，并把"启动匹配"的命令留给了本任务。

本任务第一轮两次尝试都因 Rikolto 缺只读打开入口和发布枚举接口而停下，308 已补上；这次从 `feature/0.4` 重做，不沿用上一轮的任何产出。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务新建 `executor.py`（编排和取数）、`operations.py`（运算库）、`areas.py`（地区码对照）、`runs.py`（运行记录），并在 `cli.py` 加运行命令。
- 802、803 的文件只调用、不改；Eltiro 的模型、契约和测试工具只读；Rikolto 的代码一律不改。需要改契约、模型或 Rikolto 时，写进 `summary.md` 并停下来问。
- 同时在跑的 808、809 用 `rematch.py`、`alerts.py`、`gaps.py`，并会改 `matching.py`，都不要碰。`cli.py` 本轮只归本任务改。
- 存储用标准库 `sqlite3`，表格用已装的 `pyarrow`，不加依赖。
- 回归测试写在 `tests/regressions/test_audit_wisteria_804.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **固定代次**：
   - 运行开始时用 `pin_generation()` 取得 token，或者用调用方给的 token。
   - 之后每次 `query_readonly` 都带这个 token，并核对返回的 `generation_token` 和它一致；不一致就中止。
   - token 无效、代次不可用：停止并报错，不回退到最新代次，不写运行结果。
   - 三个视图一律用 308 的 `open_generation_views` 取得，交给 802、803；不自己拼路径、不可写打开任何 Rikolto 库。
2. **画像与匹配**：
   - 该代次的画像不在画像库里时，调用 802 生成（有上一代次就增量）。
   - 然后调用 803 的 `match_demand`。
3. **只用确定的绑定**：每个指标只用人确认（`confirmed`）或自动绑定（`auto_bind`）的序列。待确认的候选不参与计算，这个指标记为"待确认"写进运行结果的缺口清单；被否决的永远不用。
4. **取数**：
   - 只用 SQL，按绑定序列的完整身份 `(source_id, table_key, indicator, indicator_code, 固定维度)` 和需求的实体、时间范围过滤。
   - 每个输入值保留 `row_evidence` 里的完整身份（含 `snapshot_sha256`、发布标识）。解析不出唯一身份就报错，不按 `obs_id` 猜。
   - `revisions: latest`：取该代次每个引用的最新发布。
   - `revisions: all`：用 `list_releases` 列出绑定序列所在来源和表的历次发布，逐项以 `as_of_release=<枚举项>` 取数；同一引用值没变的发布去重。方法写进 `docs/eltiro/executor.md` 并测试。
5. **对齐**：
   - **地区**：`areas.py` 放 ISO 3166 的二位、三位字母码和数字码对照，注明来源。来源的地区码格式从画像和来源元数据判断；判断不了或对不上就不对齐，记为缺口，不猜。
   - **时间**：按需求的频率对齐；频率不同的序列不自动升降频，记为缺口。
   - **单位**：`require_same_unit: true` 时单位不同的不对齐，报出来；不自动换算。
6. **运算库**（`operations.py`）：实现需求模型里的全部运算：`rescale`、`diff`、`diff_lag`、`ratio`、`growth_rate`、`source_spread`、`revision_diff`、`mirror_diff`、`residual`、`moving_average`、`lead_lag_corr`。
   - 全程用 `Decimal`，不用浮点。
   - 缺值保持缺值，不补零。分母为零给缺值，并记下原因。
   - 每个运算的定义写进文档。
   - **`rescale`**：倍率只取画像里有来源依据的 `Scale`，并把依据记进运算链；倍率未确认就不算，记为缺口。
   - 不做需求里没写的换算。
7. **出处**：结果的每一行都带全部输入的 `InputObservation` 和运算链（`CitationEntry` 的形状）。
8. **运行结果**：
   - 写在 `<data>/eltiro/runs/<run_id>/`：数据（Parquet）、逐行出处、缺口清单、运行记录（需求 id 和版本、代次和 token、开始和结束时间、用到的绑定）。
   - 写入是原子的，失败不留下半成品。
   - 不写使用方的发件目录；交付由 805 负责。
9. **命令行**：
   - `eltiro run <demand_id> [--version N] [--generation-token T]`：执行上面全部步骤，输出运行 id 和计数。
   - `eltiro runs list`、`eltiro runs show <run_id>`。
   - 错误信息不带需求原文和数值。
10. **回归测试** `test_audit_wisteria_804.py` 覆盖：
    - 运行中发布了新代次，本次仍只读原代次（不混读）；
    - token 无效或代次被改动时停止、不回退、不留结果；
    - 返回的 token 和固定的不一致时中止；
    - 只用 `confirmed` 或 `auto_bind` 的绑定，`pending` 的进缺口；
    - 跨表同 `obs_id` 不串；
    - `revisions: all` 取到同一代次里的历次发布（含同一天先后两次），`revision_diff` 相对首次发布计算正确；
    - 每个运算各有一个用例，含缺值和分母为零；
    - 倍率未确认时 `rescale` 不算并记缺口；
    - 单位不同时不对齐；
    - 地区码对不上时不对齐、不猜；
    - 数值只来自 SQL：用记录调用的假实现断言，没有别的取数路径；
    - 不调用 Rikolto 的任何写接口；
    - 两个样例需求（`docs/plans/0.4-wisteria-samples/`）在合成数据上能端到端跑出结果。
11. `make check T=tests` 全部通过。

# 不做
- 不做输出包、许可闸门、差异报告（805），不写使用方发件目录。
- 不做增量重匹配、提醒（808），也不做缺口汇总报告（809）。
- 不调用模型。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
