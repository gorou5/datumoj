---
id: DATUMOJ-WISTERIA-811
title: Eltiro 命令行补齐：增量重匹配与提醒、缺口报告、概念说明、需求页面草稿
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-WISTERIA-805", "DATUMOJ-WISTERIA-807", "DATUMOJ-WISTERIA-808", "DATUMOJ-WISTERIA-809", "DATUMOJ-WISTERIA-810"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/eltiro/cli.py", "docs/eltiro/cli.md", "tests/eltiro/test_cli.py", "tests/regressions/test_audit_wisteria_811.py", "results/DATUMOJ-WISTERIA-811/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "sources/**", "schemas/**", "eval/**", "src/datumoj/rikolto/**", "src/datumoj/eltiro/models.py", "src/datumoj/eltiro/contracts.py", "src/datumoj/eltiro/intake.py", "src/datumoj/eltiro/registry_store.py", "src/datumoj/eltiro/profiles.py", "src/datumoj/eltiro/profile_store.py", "src/datumoj/eltiro/matching.py", "src/datumoj/eltiro/match_store.py", "src/datumoj/eltiro/eval.py", "src/datumoj/eltiro/routing.py", "src/datumoj/eltiro/model_cache.py", "src/datumoj/eltiro/providers.py", "src/datumoj/eltiro/prompts.py", "src/datumoj/eltiro/executor.py", "src/datumoj/eltiro/operations.py", "src/datumoj/eltiro/areas.py", "src/datumoj/eltiro/runs.py", "src/datumoj/eltiro/rematch.py", "src/datumoj/eltiro/alerts.py", "src/datumoj/eltiro/gaps.py", "src/datumoj/eltiro/package.py", "src/datumoj/eltiro/license_gate.py", "src/datumoj/eltiro/model_match.py", "src/datumoj/eltiro/concept_notes.py", "src/datumoj/eltiro/drafts.py", "src/datumoj/eltiro/page_cases.py"]
protected: ["tests/fixtures/**", "src/datumoj/eltiro/testing/**", "docs/plans/0.4-wisteria-samples/**", "tests/regressions/test_audit_wisteria_804.py", "tests/regressions/test_audit_wisteria_805.py", "tests/regressions/test_audit_wisteria_807.py", "tests/regressions/test_audit_wisteria_808.py", "tests/regressions/test_audit_wisteria_809.py", "tests/regressions/test_audit_wisteria_810.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_811.py
    - cmd: make check T=tests/eltiro
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-811/summary.md 完成说明（含新增命令一览、每个命令调用的 Python 接口、错误输出规则）"]
  review: {by: other_family, checklist: ["命令是否只调用 805、807、808、809、810 已有的 Python 接口、没有重写逻辑", "固定代次的读取是否经过 open_generation_views", "错误输出是否不带需求原文、数值和来源文字", "提醒和缺口按使用方查看时是否不混入其他使用方", "草稿命令是否不登记需求", "已有命令的行为是否不变", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
808、809、807、810 这一轮只给了 Python 接口。本任务把它们接到 `eltiro` 命令行上，让宿主能在本机直接用。

# 背景
开发线：Eltiro。已完成的实现（本任务只调用，不改）：
- 808：`refresh(new_generation, previous_generation, …)`、`list_alerts`、`ack_alert`；
- 809：`gap_report`、`aggregate_gaps`；
- 807：`generate_concept_notes`、`list_concept_notes`，以及 `match_demand` / `refresh` 的模型档参数；
- 810：`parse_cases`、`record_cases`、`draft_for_case`、`list_drafts`；
- 308：`open_generation_views`；806：`load_routing`、`SQLiteModelRouter`、`SQLiteModelCache`；
- 现有 `cli.py` 的写法：`_state_path`、`_registry`、`SourceDirectory`、`_error()` 统一的固定错误输出。

第一轮两次尝试因为 `drafts make <case_no>` 拿不到完整案例而停下（`record_cases` 只存哈希），已改成命令同时接收页面文件。第二轮两次尝试因为带模型的命令缺少上下文约定、`--generation N` 怎样找历史代次不清楚而停下，已改成本轮不调用模型、`refresh` 按 token 固定代次。这次从 `feature/0.4` 重做，不沿用上一轮的任何产出。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 本任务只改 `cli.py` 和它的测试、文档。
- 其他 Eltiro 文件只调用、不改；Eltiro 的模型、契约和测试工具只读；Rikolto 的代码一律不改。发现接口缺东西时，写进 `summary.md` 并停下来问，不顺手改别的模块。
- 回归测试写在 `tests/regressions/test_audit_wisteria_811.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`；不调用真实模型。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **增量重匹配与提醒**：
   - `eltiro refresh --previous M --as-of YYYY-MM-DD [--generation-token T]`：用 `open_generation_views` 固定新代次（不给 token 就取当前已发布代次，给了就严格用它；不按代次编号去找历史代次），调用 808 的 `refresh`，模型档保持关闭。上一代次 M 只从画像库和匹配库读。输出计数。
   - `eltiro alerts list [--consumer C] [--demand D] [--all]`、`eltiro alerts ack <alert_id>`。
2. **缺口报告**：
   - `eltiro gaps show <demand_id> [--version N] --generation N`：调用 `gap_report`，输出报告路径和摘要计数。
   - `eltiro gaps report --generation N`：调用 `aggregate_gaps`，输出宿主汇总报告的路径。
3. **概念说明**：只做 `eltiro notes list --generation N`（只读概念说明库）。生成概念说明必须用模型，本轮不做。
4. **需求页面草稿**：
   - `eltiro pages record <verify|cases> <file>`：读本地页面文件，调用 `record_cases`，输出新增 / 改动 / 消失 / 未变。
   - `eltiro drafts make <verify|cases> <file> <case_no> --consumer C`：读本地页面文件，用 `parse_cases` 解析后按编号取出案例，再调用 `draft_for_case`；编号不存在或该案例解析失败时报错。`record_cases` 只存哈希，不用它取案例内容，也不为此新增存储。
   - `eltiro drafts list`。草稿命令不登记需求，输出里提示用 `eltiro submit` 提交改好的文件。
5. **共同规则**：
   - 错误一律走现有的固定错误输出，不带需求原文、数值、来源文字和路径细节；退出码非零。
   - 不联网（页面由宿主先下到本地）。
   - **本轮命令行一律不调用模型**：没有 `--model` 选项，不生成概念说明。H0 之前不能开模型，带模型的命令行（页面来源身份和许可、预算、代次上下文怎样传）以后单独定。
   - `gaps`、`notes list` 的 `--generation N` 只读 Eltiro 自己的库，不打开 Rikolto；只有 `refresh` 需要固定 Rikolto 代次。
   - 已有命令的行为、参数和输出不变。
   - `docs/eltiro/cli.md` 列出全部命令和对应的 Python 接口。
6. **回归测试** `test_audit_wisteria_811.py` 覆盖：每个新命令一个成功用例和一个失败用例；错误输出不含需求原文和数值；`alerts list --consumer` 不含其他使用方；草稿命令后需求注册表不变；已有命令的输出不变。
7. `make check T=tests` 全部通过，804、805、807、808、809、810 的回归测试原样通过。

# 不做
- 不改任何业务模块，不加新功能。
- 不做管理页面和每周报告（601），不做网络接口。
- 不改 Rikolto 的代码，不改 Eltiro 的模型和契约。
