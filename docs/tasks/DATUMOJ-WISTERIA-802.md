---
id: DATUMOJ-WISTERIA-802
title: Eltiro 序列登记：从观测值、目录、文档生成画像，按代次增量更新，三种可得程度
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-003"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "tests/eltiro/test_profiles.py", "tests/eltiro/test_profile_store.py", "tests/regressions/test_audit_wisteria_802.py", "results/DATUMOJ-WISTERIA-802/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/**", "sources/**", "schemas/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/eval.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_802.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-802/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否只读 Rikolto、不写它的库", "画像是否只用来源自带的信息、不臆造单位或倍率", "三种可得程度是否分对", "增量更新是否只处理变化部分且结果和全量重建一致", "持久化画像库是否通过 001 的契约测试套件", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
为 Rikolto 资料库里的内容生成 Eltiro 的**序列画像**（`SeriesProfile`）：观测值表里的每个序列、目录里只有元数据的数据集、文档；按数据代次存进 Eltiro 自己的画像库，新代次只处理变化的部分。

# 背景
开发线：Eltiro。契约见 WISTERIA-001（`SeriesProfile`、`SeriesIdentity`、`ProfileStore` 和它的契约测试套件）；设计见 `docs/plans/0.4-wisteria.md` 第 6.2 节和 ADR 0006（Eltiro 只读 Rikolto，不改它的库）。匹配（803）以画像为输入。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务只新建 `eltiro/profiles.py`（生成）和 `eltiro/profile_store.py`（持久化）；Eltiro 的模型、契约和测试工具只读，Rikolto 的代码一律不改。同时在跑的 801 改 `models.py`、`cli.py`，701 建 `eval.py`，都不要碰。需要改契约或模型时写进 `summary.md` 并停下来问。
- 本任务不加命令行入口（之后由 803/804 接上）；提供 Python 接口即可。
- 存储用标准库 `sqlite3`，不加依赖。
- 回归测试写在 `tests/regressions/test_audit_wisteria_802.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`；用 Rikolto `testing/` 的假实现和 `tests/fixtures/` 的样本构造数据。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **输入**：生成函数接收调用方给的**代次号**和 Rikolto 的只读接口（观测值库的 `describe` 和只读查询、目录库、文档块库、注册表）；怎样取得已发布代次由调用方负责，本任务不读状态存储。
2. **观测值序列** → `availability=observations`：每个 `(source_id, table_key, indicator, indicator_code, 固定维度)` 一个画像；单位、地区、时间覆盖、频率从观测值和表元数据取；概念编号优先用来源自带的代码（指标代码、SDMX 概念、e-Stat 表号）。倍率没有来源依据就标"未确认"，不猜。
3. **目录数据集** → `availability=catalog_only`：在目录里但没有观测值的数据集；名称和说明按语言取自目录元数据。
4. **文档** → `availability=document_only`：按来源和文档类型聚合成画像（shortcut：不做主题聚类，聚类留给以后；在代码里写一行注释说明）。
5. **多语言**：名称和说明保留来源给的各语言版本，不翻译；不用模型（模型生成的说明在 807）。`model_generated_fields` 一律为空。
6. **增量更新**（2026-10-27 改写：前两次尝试指出 `ChangeLog` 只能按时间筛、事件没有代次号，不能用它判断两代之间的变化；本任务不改契约，改用内容指纹）：
   - 给出上一个代次号时，对每个输入单元（观测值的 `(source_id, table_key)`、一个目录数据集、一个来源的一类文档）算**内容指纹**：只用 Rikolto 只读接口能读到的内容（如该表各行的 `snapshot_sha256` 集合、目录记录和文档块的内容哈希），排序后取 sha256；
   - 指纹和上一代次记录的一样就复制上一代次的画像，不一样或新出现就重新生成；上一代次有、这一代次没有的单元不再出现；
   - 指纹只存在 `profile_store.py` 自己的 sqlite 表里，不进 `SeriesProfile`，不改 Eltiro 的模型和契约；
   - 不使用 `ChangeLog`；
   - 结果必须和对同一数据全量重建一致（测试断言）。
7. **持久化**：`profile_store.py` 用 sqlite 实现 `ProfileStore`，存在 `<data>/eltiro/profiles.sqlite`，通过 001 的契约测试套件；写入一个代次是原子的。
8. **回归测试** `test_audit_wisteria_802.py` 覆盖：三种可得程度各自分对；同 `obs_id` 不同表不合并成一个序列；没有来源依据的倍率标未确认；增量结果等于全量重建；只改一张表时只有它被重新生成（用记录调用的假实现断言）；删除的表不残留；生成过程不调用 Rikolto 任何写接口（用记录调用的假实现断言）。
9. `make check T=tests` 全部通过。

# 不做
- 不做匹配（803）、不调用模型（807）、不加命令行。
- 不改 Rikolto 的任何代码，不改 Eltiro 的模型和契约。
