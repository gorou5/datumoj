---
id: DATUMOJ-WISTERIA-102
title: 来源许可等级：LicenseInfo 加 outflow 和 cloud_model_ok，迁移全部现有来源，注册表校验
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-002"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/registry.py", "src/datumoj/rikolto/testing/contract_suites.py", "src/datumoj/rikolto/testing/samples.py", "schemas/*.schema.json", "sources/*.yaml", "sources/README.md", "tests/core/test_models.py", "tests/core/test_registry.py", "tests/core/test_registry_export.py", "tests/core/test_contracts.py", "tests/analysis/test_tools.py", "tests/analysis/test_list_changes.py", "tests/analysis/test_catalog_citation.py", "tests/regressions/test_audit_wisteria_102.py", "results/DATUMOJ-WISTERIA-102/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/**", "catalog/**", "src/datumoj/eltiro/**", "src/datumoj/rikolto/analysis/**", "src/datumoj/rikolto/core/registry_export.py", "src/datumoj/rikolto/core/contracts.py"]
protected: ["tests/fixtures/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_102.py
    - cmd: make check T=tests/core/test_registry.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-102/summary.md 完成说明（含每个来源迁移前后的取值表）"]
  review: {by: other_family, checklist: ["新字段缺省是否最严", "新旧字段矛盾时是否拒绝", "放宽许可是否必须带名称、官方 URL、核对日期和依据", "迁移是否没有从旧字段推断出 derived_only 或 cloud_model_ok=true", "现有来源的取值和行为是否不变", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
来源许可增加两个字段：**`outflow`**（能不能流出：`raw_ok` / `derived_only` / `no_outflow`）和 **`cloud_model_ok`**（能不能交给云端模型处理），放进现有的 `LicenseInfo`（`sources/*.yaml` 的 `license`）；全部现有来源迁移到新字段，注册表校验"缺省最严、矛盾拒绝"。

# 背景
开发线：A。Eltiro 的输出闸门（805）和模型路由（806）要按来源许可决定数据能不能交出、能不能进云端模型。设计见 `docs/plans/0.4-wisteria.md` 第 6.6 节。`Outflow` 枚举已由 WISTERIA-001 放在 `rikolto/core/models.py`。注册表校验过的 `LicenseInfo` 是唯一权威：Eltiro 的画像、输出清单和缓存里都不复制许可，使用方配置只能再收紧。

现状：7 个来源全部是 `usage_scope: internal_only`、`redistribution: unverified`。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 回归测试写在 `tests/regressions/test_audit_wisteria_102.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- `tests/fixtures/` 只读。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **新字段**：`LicenseInfo` 加 `outflow: Outflow`（缺省 `no_outflow`）和 `cloud_model_ok: bool`（缺省 `false`，严格布尔）。
2. **新旧字段一致**：旧字段 `usage_scope` / `redistribution` 保留，作为新字段推出的兼容值：`outflow=raw_ok` 当且仅当 `usage_scope=redistributable` 且 `redistribution=allowed`。两者矛盾的 `LicenseInfo` 在模型校验时就拒绝（不只在注册表里），错误信息写清哪两个字段冲突。
3. **放宽要有依据**：`outflow` 不是 `no_outflow`，或 `cloud_model_ok=true` 时，注册表要求 `name`、`url`、`verified_at` 和 `notes`（写依据）都已填写，缺了就拒绝；`raw_ok` 还沿用现有的 `attribution` 要求。
4. **迁移**：全部 `sources/*.yaml` 显式写上 `outflow` 和 `cloud_model_ok`。按计划第 6.6 节：`redistributable` + `allowed` → `raw_ok`，其余 → `no_outflow`；`cloud_model_ok` 一律 `false`。**不从旧字段推断出 `derived_only` 或 `cloud_model_ok=true`**，这两种只能以后由 H2 按条款核准后改。
5. **已有代码不受影响**：引用、`usage_notice`、目录导出仍按旧字段工作，行为不变；只为通过新的一致性校验，更新测试里构造 `redistributable` 许可的地方（补上 `outflow=raw_ok`）。不改 `analysis/` 和 `registry_export.py` 的代码。
6. **Schema**：重新导出 `schemas/`，和仓库一致。
7. **文档**：`sources/README.md` 的 `license` 字段说明加上两个新字段、取值含义、缺省值和放宽所需的依据。
8. **回归测试** `test_audit_wisteria_102.py` 覆盖：缺省最严；四种矛盾组合被拒绝；放宽缺依据被拒绝；每个现有来源迁移后的取值（`no_outflow` / `false`）；迁移前后的引用和 `usage_notice` 不变。
9. `make check T=tests` 全部通过。`summary.md` 附每个来源迁移前后的取值表。

# 不做
- 不实现输出闸门和模型路由（805、806），不改 Eltiro 的代码。
- 不把任何来源放宽到 `derived_only`、`raw_ok` 或 `cloud_model_ok=true`（H2）。
- 不删旧字段，不改目录导出格式。
