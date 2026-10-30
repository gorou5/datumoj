---
id: DATUMOJ-WISTERIA-801
title: Eltiro 需求注入：校验、收件与发件目录、使用方身份与权限、审计记录、命令行
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-003"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eltiro/models.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/cli.py", "src/datumoj/eltiro/__main__.py", "pyproject.toml", "schemas/eltiro/**", "docs/eltiro/intake.md", "tests/eltiro/test_intake.py", "tests/eltiro/test_registry_store.py", "tests/eltiro/test_cli.py", "tests/eltiro/test_models.py", "tests/regressions/test_audit_wisteria_801.py", "results/DATUMOJ-WISTERIA-801/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "sources/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "src/datumoj/eltiro/eval.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_801.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-801/summary.md 完成说明"]
  review: {by: human, checklist: ["收件目录里的文件是否只当数据、先查大小再解析、不跟随符号链接", "需求的 consumer 和收件目录的使用方对不上时是否拒绝", "使用方能用的来源、许可、模型是否只能比注册表和需求更严", "同一 (id, version) 是否不能被改写", "回执是否不回显需求原文、不泄露其他使用方的信息", "审计记录是否只追加、记下文件哈希和结论", "使用方配置是否放在数据目录、不进仓库、不含凭证值", "持久化登记是否通过 001 的契约测试套件", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
外部项目可以把需求文件放进自己的收件目录，或用命令提交；Eltiro 校验后登记（持久化），把回执写进该使用方的发件目录，并留下审计记录。

# 背景
开发线：Eltiro。契约见 WISTERIA-001（`DemandSpec`、`load_demand`、`DemandRegistry` 和它的契约测试套件）和 003（`ConsumerModelConfig`）；设计见 `docs/plans/0.4-wisteria.md` 第 6.1 节和 ADR 0006（注入是信任边界）。注入入口是本机文件和命令行，不做网络接口（计划第 5 节）。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务只动 Eltiro。`eltiro/models.py` 可以加使用方配置等新模型（并重新导出 `schemas/eltiro/`），但已有模型的定义不改；`eltiro/contracts.py` 和 `eltiro/testing/` 只读。同时在跑的 802（画像）和 701（评测）各自用新文件，不要碰 `profiles.py`、`profile_store.py`、`eval.py`。
- `pyproject.toml` 只许在 `[project.scripts]` 加一行 `eltiro = "datumoj.eltiro.cli:app"`；`uv.lock` 不能变。
- 存储用标准库 `sqlite3`，不加依赖。
- 回归测试写在 `tests/regressions/test_audit_wisteria_801.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **使用方配置**：放在数据目录 `<data>/eltiro/consumers.yaml`（不进仓库）。每个使用方：`id`、收件目录、发件目录、能用的来源（注册表 id 列表）、能接受的许可等级、模型限制（`ConsumerModelConfig`）、在用需求数上限。模型校验拒绝未知字段；目录必须在数据目录之内（解析后比较真实路径），不同使用方的目录不能重叠。提供 `docs/eltiro/intake.md` 写明格式（示例用虚构值）。
2. **持久化登记**：`registry_store.py` 用 sqlite 实现 `DemandRegistry`，通过 001 的契约测试套件；同一 `(id, version)` 只能登记一次（内容相同幂等，不同就拒绝）；记录登记时间、使用方、文件 sha256、在用状态。
3. **收件处理**：扫描每个使用方的收件目录：
   - 只处理普通文件（不跟随符号链接，跳过目录和隐藏文件），文件名只允许安全字符；
   - 先查大小再读（沿用 `load_demand` 的上限），用 `load_demand` 校验；
   - 需求里的 `consumer` 必须等于收件目录所属的使用方，否则拒绝；
   - 偏好来源必须在注册表里、且在该使用方能用的来源内；`accept_outflow` 和模型设置只能比使用方配置更严，超出的拒绝（不悄悄收紧）；
   - 超过在用需求数上限就拒绝；
   - 通过的登记并移到 `processed/`，不通过的移到 `rejected/`；移动是原子的，同名不覆盖。
4. **回执**：每个文件在该使用方的发件目录写一份 JSON 回执：结论（accepted / rejected）、需求 id 和版本（能解析时）、文件 sha256、原因代码和简短说明。**不回显需求原文**，不提及其他使用方。
5. **审计记录**：`<data>/eltiro/audit.jsonl` 只追加：时间、使用方、文件 sha256、结论、原因代码；不写需求原文和文件路径以外的内容。
6. **命令行**（`eltiro`）：`eltiro intake`（处理全部收件目录，输出计数）、`eltiro submit --consumer <id> <file>`（等同于放进收件目录后处理这一份）、`eltiro demands list`、`eltiro demands show <id> [--version N]`、`eltiro demands deactivate <id> --version N`。错误信息不带文件内容。
7. **回归测试** `test_audit_wisteria_801.py` 覆盖：两个样例需求能登记并出 accepted 回执；consumer 不符、来源不在许可范围、放宽许可或模型、超上限、超大文件、符号链接、未知字段、同 (id, version) 改内容都被拒绝并有回执和审计；回执不含需求原文；目录越出数据目录或互相重叠的配置被拒绝；sqlite 登记通过契约测试套件。
8. `make check T=tests` 全部通过。

# 不做
- 不做网络接口和主动推送。
- 不做匹配、执行和输出（803–805）。
- 不改 Rikolto 的代码。
