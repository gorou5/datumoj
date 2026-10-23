---
id: DATUMOJ-MAPLE-900
title: 检查点 m0：证据正确性（回归全部通过；本机数据只读诊断与重放迁移工具）
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-002", "DATUMOJ-MAPLE-107", "DATUMOJ-MAPLE-108", "DATUMOJ-MAPLE-109", "DATUMOJ-MAPLE-110", "DATUMOJ-MAPLE-111", "DATUMOJ-MAPLE-202", "DATUMOJ-MAPLE-203", "DATUMOJ-MAPLE-306", "DATUMOJ-MAPLE-307", "DATUMOJ-MAPLE-308", "DATUMOJ-MAPLE-404", "DATUMOJ-MAPLE-502", "DATUMOJ-MAPLE-503", "DATUMOJ-MAPLE-602"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/diagnose.py", "src/datumoj/cli.py", "src/datumoj/wiring.py", "src/datumoj/mcp_server.py", "tests/integration/test_maple_m0.py", "tests/test_evidence_regressions.py", "tests/regressions/test_audit_900.py", "tests/test_diagnose.py", "results/DATUMOJ-MAPLE-900/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_maple_m0.py
    - cmd: make check T=tests/test_diagnose.py
    - cmd: make check T=tests/regressions
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-900/summary.md 完成说明"]
  review: {by: human, checklist: ["10 个审核场景是否都有回归测试并通过", "diagnose 是否真正只读（含不触发迁移）", "replay 是否拒绝非空目标、是否给出对比", "是否只改了接线、CLI、MCP 说明、诊断模块及 scope 列出的测试", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
审核的 13 个问题都有回归测试并全部通过；能对本机已有数据做只读诊断（列出哪些地方受这些问题影响），并能把已有数据按新规则重放到一个新目录，比对后再决定是否切换。

# 背景
开发线：集成。MAPLE 检查点 m0（计划 6.0 节“顺序”）：修完之后才动真实数据。人会在本机数据的副本上运行诊断和重放（H3 之前），结果记到 `docs/acceptance/v0.3.0-maple-m0.md`。本地数据库的 schema 已由 002 / 107 / 111 升级（各自带迁移）。只允许改接线、CLI、MCP 说明文字、新的诊断模块，以及 scope 列出的诊断测试、`tests/test_evidence_regressions.py` 和新增集成 / 审核回归测试；测试修改须服务于本任务的要求与验收，不扩大到各开发线模块。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-900/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `tests/regressions/` 里覆盖 ADR 0004 列出的全部 10 个审核场景（缺哪个就在 `tests/regressions/test_audit_900.py` 补上），全部通过。
2. `datumoj diagnose evidence [--data-dir DIR]`（新模块 `diagnose.py`）：**只读**打开数据目录（SQLite 用只读 URI，不触发 schema 迁移；遇到旧 schema 时用只读方式按旧结构读），输出 JSON 报告：同 SHA 多资源的快照组、旧失败 + 新成功的资源、跨表重名的 `obs_id`、current 与按快照顺序应发布版本不符的 doc_key、raw 校验失败的 blob（可用 `--verify-raw` 打开，默认不做，因为要读全部 raw）、PDF 块缺日期的文档数；每类给出数量和最多 20 个样例。运行前后数据目录的文件内容不变（测试断言）。
3. `datumoj replay --from DIR --to NEWDIR`：把旧目录的 manifest 和 raw 复制（raw 可用硬链接）到新目录，按快照顺序重新解析、建关键词索引（向量用 `--vectors` 才建），输出新旧对比（文档数、块数、current 不同的 doc_key 数和样例、观测值表数）；目标目录非空时拒绝。
4. 集成测试 `tests/integration/test_maple_m0.py`：用合成数据造出 D-01–D-04、D-06、D-09 的“旧数据”（用旧版行为写入的库，测试里直接写库构造），`diagnose` 能逐类报出；`replay` 后新目录里这些问题都消失，`diagnose` 报告为零。
5. MCP：`query_sql` 的说明补上“需要逐行出处时 SELECT 带上 source_id、table_key、obs_id”（306 的遗留）；`cite` 说明与 404 一致。
6. （固定要求）集成测试**不能写入仓库里受 git 跟踪的路径**（包括 `results/`）：证据默认写到临时目录，只有设置了环境变量 `DATUMOJ_EVIDENCE_DIR` 时才写到该目录（参考 `tests/integration/test_law_e2e.py` 的写法）。
7. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改各开发线的模块（接线和 CLI 除外）。
- 不碰真实数据目录；诊断和重放的真实运行由人做。
