---
id: DATUMOJ-WISTERIA-805
title: Eltiro 输出包与许可闸门：数据文件、清单、逐行出处与引用索引、缺口、差异；按数据血缘检查许可，不通过整包不交出
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-102", "DATUMOJ-WISTERIA-804", "DATUMOJ-WISTERIA-809", "DATUMOJ-MAPLE-306", "DATUMOJ-MAPLE-307"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/eltiro/package.py", "src/datumoj/eltiro/license_gate.py", "src/datumoj/eltiro/cli.py", "docs/eltiro/package.md", "tests/eltiro/test_package.py", "tests/eltiro/test_license_gate.py", "tests/eltiro/test_cli.py", "tests/regressions/test_audit_wisteria_805.py", "results/DATUMOJ-WISTERIA-805/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "src/datumoj/eltiro/matching.py", "src/datumoj/eltiro/match_store.py", "src/datumoj/eltiro/eval.py", "src/datumoj/eltiro/routing.py", "src/datumoj/eltiro/model_cache.py", "src/datumoj/eltiro/providers.py", "src/datumoj/eltiro/prompts.py", "src/datumoj/eltiro/executor.py", "src/datumoj/eltiro/operations.py", "src/datumoj/eltiro/areas.py", "src/datumoj/eltiro/runs.py", "src/datumoj/eltiro/rematch.py", "src/datumoj/eltiro/alerts.py", "src/datumoj/eltiro/gaps.py", "src/datumoj/eltiro/model_match.py", "src/datumoj/eltiro/concept_notes.py", "src/datumoj/eltiro/drafts.py", "src/datumoj/eltiro/page_cases.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**", "tests/regressions/test_audit_wisteria_804.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_805.py
    - cmd: make check T=tests/regressions/test_audit_wisteria_804.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-805/summary.md 完成说明（含输出包目录结构、闸门逐列检查的规则、三种许可等级各自输出什么、两个样例需求在合成数据上打出的包）"]
  review: {by: human, checklist: ["许可是否只从注册表取、画像和运行记录里的副本不能放宽", "no_outflow 来源的数据是否不进任何输出", "derived_only 来源是否只出数值、不出原文、注记和文本片段，出处只给引用编号", "需求和使用方的 accept_outflow 是否只能收紧", "闸门不通过时是否整包不交出、发件目录里不留半成品", "清单、逐行出处、引用索引是否能解析到每个输入的完整身份", "差异是否只和同一需求同一使用方的上一次交付比", "缺口是否合并了运行缺口和 809 的缺口报告、且不提及其他使用方", "是否只写这个使用方的发件目录", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
把 804 的一次运行结果打成交给使用方的输出包，写进使用方的发件目录。写出前按数据血缘逐列检查许可，不通过就整包不交出，并说明原因。

# 背景
开发线：Eltiro。设计见 `docs/plans/0.4-wisteria.md` 第 6.5 节"输出包"、第 6.6 节许可闸门、第 3.3 节原始物与最终结果的界线。用到的契约和已完成的实现：
- 001 的 `OutputManifest`、`DataFile`、`LicenseSummary`、`CitationEntry`、`InputObservation`、`ModelUse`，需求的 `output`（字段和格式）、`accept_outflow`，使用方配置的 `accept_outflow` 和发件目录；
- 102 的 `LicenseInfo.outflow`（`raw_ok` / `derived_only` / `no_outflow`），注册表是唯一权威；
- MAPLE 306 / 307 的观测值引用键和证据状态；
- 804 的运行目录 `<data>/eltiro/runs/<run_id>/`：`data.parquet`、`citations.json`、`gaps.json`、`record.json`；`RunStore.show`；
- 809 的 `gap_report`；
- 801 的 `open_directory` 和发件目录约定。

804 的运行结果里有来自来源的文字列（例如 `note` 注记、序列的指标名称）。按第 3.3 节，这些属于原文，`derived_only` 的来源不能出现在输出里。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务新建 `package.py`（打包、差异、写出）和 `license_gate.py`（闸门），并在 `cli.py` 加命令。
- 804、809 的文件只调用、不改；Eltiro 的模型、契约和测试工具只读；Rikolto 的代码一律不改。需要改契约或模型时，写进 `summary.md` 并停下来问。
- 同时在跑的 807、810 用各自的文件，都不要碰。`cli.py` 本轮归本任务改，之后由 811 接着改。
- 存储用标准库，表格用已装的 `pyarrow`，不加依赖。
- 回归测试写在 `tests/regressions/test_audit_wisteria_805.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **许可来源**：每个来源的许可等级只从注册表（宿主传入的 `SourceSpec`）取。画像、运行记录、缓存里的副本只作审计，不能放宽。注册表里查不到的来源按 `no_outflow` 处理。
2. **闸门**（`license_gate.py`）在任何文件写出前逐列、逐行检查数据血缘：
   - 每一行的许可等级取它全部输入来源里最严的一级；
   - 含 `no_outflow` 来源输入的行不能出现在输出里。需求要的字段因此缺数据时，整包不交出，原因写明是哪个来源的许可，不写数值；
   - 行的许可等级必须同时在需求的 `accept_outflow` 和使用方的 `accept_outflow` 里，否则整包不交出；
   - `derived_only` 来源的行只输出数值和标识：去掉注记、指标名称等来源文字；出处只给引用编号和输入身份，不附原文；
   - `raw_ok` 的行可以带注记。
   - 闸门的判定写进包内清单的 `license_summary`，并在运行目录旁记一份审计记录。
3. **输出包内容**：
   - 数据文件：按需求 `output.format` 出 Parquet 和/或 CSV，只含 `output.fields` 列出的符号；数值保持 Decimal 精度，CSV 用十进制字符串，不转浮点；
   - `manifest.json`：`OutputManifest`，含需求 id 和版本、代次和 token、运行时间、使用的模型（804 的运行没有就为空）、许可汇总、数据文件及其 sha256、引用索引；
   - 逐行出处：每行一个引用编号，引用索引能解析到全部输入的完整身份（`source_id`、`table_key`、`obs_id`、`snapshot_sha256`、发布标识）和运算链；
   - 缺口：合并 804 运行里的缺口和 809 对同一需求、同一代次的缺口报告；只含这个需求自己的内容，不提及其他使用方；
   - 差异：和同一使用方、同一需求的上一次成功交付比较，列出新增、删除、数值变化的行（按 符号、实体、时间、来源、发布 对齐）；第一次交付标明没有可比的上次。
4. **写出**：
   - 写到 `<发件目录>/<demand_id>/v<version>/<run_id>/`，先写临时目录、全部写完并 fsync 后一次改名发布；失败不留下半成品，发件目录里原有的包不受影响；
   - 只写这个使用方的发件目录，拒绝符号链接和逃出目录的路径；
   - 同一运行重复打包得到字节相同的包（时间戳取运行记录，不取当前时间），已存在时不覆盖、直接返回。
5. **Python 接口**：`package_run(run_id, *, data_root, registry, consumers, demands)`，返回包的路径和计数。
6. **命令行**：`eltiro package <run_id>`：打包并输出包路径和计数；闸门不通过时退出码非零，只输出固定的原因代码和来源 id，不带数值和需求原文。
7. **回归测试** `test_audit_wisteria_805.py` 覆盖：
   - 三种许可等级各自的输出：`raw_ok` 带注记，`derived_only` 只有数值和标识，`no_outflow` 不出现；
   - 运行记录或画像里把许可写成更宽的值，闸门仍按注册表；
   - 需求或使用方的 `accept_outflow` 收紧时整包不交出；
   - 闸门不通过、写出中途失败时，发件目录里没有新文件，原有的包不变；
   - 包里任何文件都不含 `derived_only` / `no_outflow` 来源的原文和注记（用带标记字符串的合成数据断言）；
   - 引用索引能解析到每行全部输入的完整身份；
   - 差异只和同一使用方同一需求的上次交付比；
   - 同一运行重复打包字节相同、不覆盖；
   - 不写其他使用方的发件目录；
   - 两个样例需求在合成数据上从运行到打包端到端通过。
8. `make check T=tests` 全部通过，804 的回归测试原样通过。

# 不做
- 不改执行器和运行目录格式（804），不做增量重匹配和提醒（808）。
- 不主动推送，不做网络接口。
- 不调用模型。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
