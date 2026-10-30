---
id: DATUMOJ-WISTERIA-001
title: Eltiro 契约：需求定义、序列画像、匹配记录、输出包清单、许可等级；ADR 0006
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-119"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eltiro/__init__.py", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/testing/**", "src/datumoj/rikolto/core/models.py", "schemas/Outflow.schema.json", "schemas/eltiro/**", "docs/adr/0006-eltiro-boundary.md", "tests/eltiro/**", "tests/regressions/test_audit_wisteria_001.py", "tests/regressions/test_audit_119.py", "results/DATUMOJ-WISTERIA-001/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "sources/**", "src/datumoj/rikolto/core/contracts.py"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/testing/**", "docs/plans/0.4-wisteria-samples/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_001.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-001/summary.md 完成说明（含样例到 schema 的字段对照表）"]
  review: {by: human, checklist: ["两个需求样例是否都能原样通过校验、表达的内容没有丢", "未知字段、超限、可执行内容是否一律拒绝", "运算是否只能从固定清单里选", "画像、匹配记录、输出清单的字段是否够后续 802–805 用、没有多余的通用元数据", "许可等级的缺省是否最严", "ADR 是否写清边界、信任边界和许可闸门", "是否没碰 Rikolto 的其他契约", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
定下 Eltiro 的契约：外部项目注入的**需求定义**、Eltiro 自己维护的**序列画像**、**匹配记录**、**输出包清单**和**许可等级**，用 pydantic 模型 + JSON Schema + 接口协议 + 假实现和契约测试表达；写 ADR 0006。只定契约，不实现匹配、执行和输出。

# 背景
开发线：契约。0.4 WISTERIA 新建子系统 Eltiro（提取）：外部项目注入"要什么数据"，Eltiro 读懂 Rikolto 资料库里已有的内容，把需求匹配到具体序列，按固定的数据代次取数、运算，交出带出处、过了许可闸门的结果。设计见 `docs/plans/0.4-wisteria.md` 第 3.3、6.1–6.6 节；需求样例在 `docs/plans/0.4-wisteria-samples/`（两个文件），schema 以它们为基准：字段名可以改，但样例表达的内容都要能表达。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。Rikolto 的模块在 `src/datumoj/rikolto/`（MAPLE 119 改名后），Eltiro 的代码放 `src/datumoj/eltiro/`。
- **Rikolto 的契约只加一样东西**：在 `rikolto/core/models.py` 加许可等级枚举 `Outflow`（见要求 5）并导出它的 schema；`LicenseInfo` 本身不改（那是 102 的事），其他定义一律不动。
- 回归测试写在 `tests/regressions/test_audit_wisteria_001.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`（YAML 用已有的库读）。
- `tests/fixtures/` 和需求样例只读；测试直接读 `docs/plans/0.4-wisteria-samples/` 下的样例。
- **前两次尝试的教训**（2026-10-27）：当时分支上缺 0.4 计划和需求样例，现已补到 `feature/0.4`（`docs/plans/0.4-wisteria.md`、`docs/plans/0.4-wisteria-samples/`）。计划正文是 #23 合并时的版本，里面"改名由 0.4 的 101 做"的说法已过时：改名已由 MAPLE 119 做完，以本任务文件为准。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **需求定义**（`eltiro/models.py` 的 `DemandSpec` 及其子模型）能表达样例里的全部内容：`schema` 版本标记、`id`、`version`、`consumer`、用途说明、案例出处、实体（国家用 ISO 3166-1 alpha-3）、时间范围和频率、要哪些发布（最新 / 全部）、指标（概念文字、单位、偏好来源用注册表 id、最少来源数、按什么分开不合并、正例反例；**不能写表名**）、对齐键和同单位要求、运算、输出字段和格式、可接受的许可等级、模型档位上限和是否允许云端、每次运行的调用上限、触发方式和提醒类型。
2. **信任边界**：所有模型 `extra="forbid"`；文件大小、指标数、实体数、运算数、文本长度有上限（取值写成常量并在 ADR 里列出）；id 只允许小写字母数字和连字符；运算只能从固定清单里选（`rescale`、`diff`、`diff_lag`、`ratio`、`growth_rate`、`source_spread`、`revision_diff`、`mirror_diff`、`residual`、`moving_average`、`lead_lag_corr`，参数各自校验），运算之间的引用必须指向已定义的指标或运算且不能成环；任何字段里的文本只当数据。提供 `load_demand(path_or_bytes)`：先查大小再解析，YAML 只用安全加载。
3. **序列画像** `SeriesProfile`：序列身份（`source_id`、`table_key`、指标、指标代码、固定维度）、来源自带的概念编号、多语言名称和说明、单位和倍率（倍率未确认要能表达）、地区和时间覆盖、频率、可得程度（`observations` / `catalog_only` / `document_only`）、生成它的数据代次、哪些字段是模型生成的。许可**不复制**进画像，只引用来源 id，由注册表的 `LicenseInfo` 说了算。
4. **匹配记录** `MatchRecord`：需求 id 和版本、指标 key、序列身份、档位（`deterministic` / `vector` / `model`）、置信度、理由、依据（画像条目和出处）、数据代次、人工确认状态（`pending` / `confirmed` / `rejected`）。口径、单位或调查不同的候选不能标成自动绑定（模型校验拒绝）。
5. **许可等级**：`Outflow` 枚举 `raw_ok` / `derived_only` / `no_outflow` 放在 Rikolto 的 `core/models.py`；需求里的可接受等级和输出清单的许可汇总都用它；缺省一律按最严（`no_outflow`）处理。
6. **输出包清单** `OutputManifest`：需求 id 和版本、数据代次和 `generation_token`（这里只当不透明字符串，格式由 002 定）、运行时间、用到的模型和档位、许可汇总、各数据文件的名字和 sha256、出处索引（引用编号 → 输入观测值的完整身份 `(source_id, table_key, obs_id)` + `snapshot_sha256` + 发布标识 + 运算链；不附原文）。
7. **接口协议**（`eltiro/contracts.py`）：需求登记（校验、登记、按 id 和版本取、列出在用需求）、画像库（按代次写入、按序列取、列出）、匹配记录库（写入、按需求取、改确认状态）。`eltiro/testing/` 放内存假实现和契约测试套件，形式照 Rikolto 的 `testing/`。
8. **Schema**：Eltiro 的模型导出到 `schemas/eltiro/`，`Outflow` 导出到 `schemas/`；重新导出和仓库里的一致（测试断言）。
9. **ADR 0006**（`docs/adr/0006-eltiro-boundary.md`）：Eltiro 和 Rikolto 的边界（只读已发布代次、不改 Rikolto 的库、自己的库放哪）、注入的信任边界和各项上限、许可闸门和"原始物 / 最终结果"的界线（计划第 3.3 节）。
10. **回归测试** `test_audit_wisteria_001.py` 覆盖：两个样例原样通过；未知字段、超限、表名写进指标、未知运算、运算成环、悬空引用、非 alpha-3 国家码都被拒绝；超大文件在解析前就被拒绝；口径不同的候选不能自动绑定；`Outflow` 缺省最严；schema 导出一致。
11. **119 的 schema 锁**：`tests/regressions/test_audit_119.py` 的 `test_schema_export_is_byte_identical` 除了断言"重新导出和仓库一致"，还用 `SCHEMAS_HASH` 锁死了整个 `schemas/` 目录的哈希，任何合法的契约变更（本任务的 `Outflow`、之后 002 改 `Observation`）都会让它失败。删掉 `SCHEMAS_HASH` 常量和这条整目录哈希断言，保留"重新导出和仓库一致"的断言；改名时 schema 没变这件事已由 119 的验收证明。`test_audit_119.py` 的其他测试不改。
12. `make check T=tests` 全部通过。`summary.md` 附样例字段到 schema 的对照表，以及刻意没定、留给后续任务的点。

# 不做
- 不实现注入目录、命令行、匹配、执行、输出和闸门（801–805）。
- 不改 `LicenseInfo` 和 `sources/*.yaml`（102）。
- 不定义观测值的发布标识和 `generation_token` 格式（002）。
- 不定义模型路由（003）。
