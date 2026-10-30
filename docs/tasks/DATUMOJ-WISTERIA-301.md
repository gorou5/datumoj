---
id: DATUMOJ-WISTERIA-301
title: 观测值按发布时间查询的实现：保留历次发布、截至查询、固定代次读取、导入历史发布
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-002", "DATUMOJ-MAPLE-306", "DATUMOJ-MAPLE-307", "DATUMOJ-MAPLE-107", "DATUMOJ-MAPLE-308"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/store/observations.py", "src/datumoj/rikolto/analysis/tools.py", "src/datumoj/rikolto/mcp_server.py", "src/datumoj/rikolto/cli.py", "src/datumoj/rikolto/wiring.py", "skills/rikolto/SKILL.md", "docs/adr/0007-observation-releases.md", "tests/store/test_observations.py", "tests/store/test_observation_refs.py", "tests/store/test_observation_releases.py", "tests/store/test_query_budget.py", "tests/analysis/test_tools.py", "tests/test_mcp_server.py", "tests/test_cli.py", "tests/core/test_contracts.py", "tests/integration/test_stats_e2e.py", "tests/integration/test_maple_m0.py", "tests/regressions/test_audit_wisteria_301.py", "tests/regressions/test_audit_119.py", "results/DATUMOJ-WISTERIA-301/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "sources/**", "src/datumoj/eltiro/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/crawlers/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/testing/**", "schemas/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_301.py
    - cmd: make check T=tests/store
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-301/summary.md 完成说明（含旧数据目录的兼容说明）"]
  review: {by: human, checklist: ["DuckDB 实现是否通过 002 的 ObservationReleaseStore 契约测试套件", "历次发布是否保留、替换 current 不删历史", "截至查询是否取对版本，同一天多次发布按 SnapshotOrder", "token 无效或代次不可用时是否报错、不回退最新", "没有代次的本机数据目录，不带 token 的查询行为是否不变", "旧 Parquet 文件是否能直接读（新增列按 NULL）", "历史发布导入是否走来源已有的解析器、带发布标识和日期、不伪造", "MCP 和命令行的新参数是否有说明", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
在 Rikolto 的观测值库里实现 WISTERIA-002 定下的契约：同一观测值的历次发布都保留；`query_sql` 能"截至某次发布"查询；能用 `generation_token` 固定在一个已发布代次上读；能把来源公开的历史发布存档导入成带发布标识的观测值。

# 背景
开发线：C。契约见 `docs/adr/0007-observation-releases.md` 和 `rikolto/core/contracts.py`（`ObservationReleaseStore`、`GenerationToken`、`ObservationIdentity`、`QueryResult.row_evidence`）；假实现和契约测试套件在 `rikolto/testing/`。试点 V07（希腊赤字修订链）要比较同一指标的多次发布，2009—2010 年的历次发布要从来源的历史存档补（`docs/plans/0.4-wisteria.md` 第 6.9 节）。Eltiro 执行器（804）用这里的固定代次读取。

现状：`store/observations.py` 的 `DuckDBObservationStore` 按 `(source_id, table_key)` 一个 Parquet 文件，`write_table` 整体替换；`analysis/tools.py` 的 `query_sql` 和 MCP 的 `query_sql` 调 `query_readonly`。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **契约不改**：`core/models.py`、`core/contracts.py`、`testing/` 和 `schemas/` 只读；实现对不上契约时写进 `summary.md` 并停下来问。ADR 0007 只能补充实现细节，不改语义。
- 回归测试写在 `tests/regressions/test_audit_wisteria_301.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- `tests/fixtures/` 只读。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **保留历次发布**：`DuckDBObservationStore` 实现 `ObservationReleaseStore`。每次成功写入的发布版本（按 `ObservationIdentity`）连同 `SnapshotOrder` 和 `fetched_at` 一起保留；`write_table` 照旧替换 current、保留水位和删除约定，但不删历史。存储布局自定（例如 current 文件 + 按表的历史文件），写在 ADR 0007 的实现说明里。
2. **兼容旧数据**：旧的 Parquet 文件不用迁移就能读，新增的 `release_id` / `released_at` 列按 NULL；没有发布标识的观测值按"一次快照即一次发布"处理（ADR 0007）。
3. **截至查询**：`query_readonly` 支持 `as_of_release`（日期或发布标识），语义完全按契约：先在固定代次的历史里按（发布日期，`SnapshotOrder`）选每个引用的最近版本，再执行 SQL；各种错误类型照契约抛。
4. **固定代次读取**：实现 `pin_generation` 和 `generation_token` 参数：读本机数据目录里已发布代次（`datumoj pull` 拉下来的或本机发布的）的清单，校验清单字节哈希；token 无效、代次不存在或依赖的文件不可用一律报错，不回退到最新。**没有任何已发布代次的本机数据目录**：不带 token 的查询行为和现在完全一样（读 current），只有显式要求固定代次时才报 `GenerationUnavailable`；把这一点写进 ADR 0007。
5. **逐行出处**：结果带 `generation_token`（固定代次时）、`row_evidence`（每行的完整身份和所读代次）、`evidence_state` 和 `evidence_inputs`（含截至条件），规则照契约；聚合行的身份可以为空。
6. **入口**：`analysis/tools.py` 的 `query_sql`、MCP 的 `query_sql` 工具加可选参数 `as_of_release` 和 `generation_token`，工具说明写清含义；不传时行为不变。`skills/rikolto/SKILL.md` 补一段：比较修订时用 `as_of_release`，并说明数据代次和来源发布的区别。
7. **导入历史发布**：命令 `rikolto observations import-release --source <id> --file <path> --release-id <id> --released-at <YYYY-MM-DD>`：文件先作为 raw 快照存进原始文件库，再用该来源已注册的解析器解析，产出的观测值带上给定的发布标识和日期写进历史（不替换 current，除非它比 current 新——规则写进 ADR）。发布标识和日期必须显式给出，不从文件名或抓取时间推断。文件大小沿用该来源的上限。测试用现有来源的样本文件。
8. **契约测试**：`tests/core/test_contracts.py` 里用 DuckDB 实现跑 002 的 `ObservationReleaseStore` 契约测试套件，全部通过。
9. **回归测试** `test_audit_wisteria_301.py` 覆盖：A→B→A 后截至早期发布仍能取到 A；替换 current 不丢历史；同一天多次发布按 `SnapshotOrder`；跨表同 `obs_id` 不串；运行中出新代次时固定 token 仍读旧代次；token 无效、代次不可用报错不回退；无代次的本机目录不带 token 查询不变；旧 Parquet 可读；导入历史发布带正确的发布标识、日期和出处；MCP 新参数不传时结果和现在一致。
10. `make check T=tests` 全部通过，包括各版本端到端测试。
11. **MCP 工具定义锁**（2026-10-27 第二次尝试失败的原因）：`tests/regressions/test_audit_119.py` 用 `MCP_TOOLS_HASH` 锁住了 MCP 工具签名，`query_sql` 加参数后它必然变。只许更新这个哈希常量（并在旁边注明是 301 加了 `as_of_release` 和 `generation_token`），该文件的其他断言不改。
12. **固定代次读取的性能**（2026-10-27 第一次人工审查打回的原因）：上一次实现中，只要本机有已发布代次，每次查询（包括不带 token 的查询）都会重新读取并哈希该代次的全部存储副本和全部 raw 快照，并把所有表的全部历史载入 Python 内存。真实数据目录有几十 GB，其中 raw 有几 GB，每次查询要几分钟，`timeout_s` 也管不到这段。改为：
   - **只校验一次**：代次的完整校验（存储副本、raw 哈希、发布状态）只在 pull、push/restore 后保留代次时做一次。校验结果记在该代次目录里：清单哈希，以及每个依赖文件的大小和修改时间。
   - **查询时只做轻量核对**：核对清单字节哈希，以及依赖文件的大小、修改时间是否和记录一致。不一致就报 `GenerationUnavailable`，不回退，也不在查询时重新全量校验。
   - **不把全部历史载入内存**：只读取 SQL 实际需要的数据。例如把历史存成 DuckDB 能直接读的列式文件，在 DuckDB 里选版本。截至查询和固定代次查询的内存占用，不随无关表的历史增长。
   - **超时**：代次加载和选版本这一段也受 `timeout_s` 限制，超时抛 `QueryTimeout`。
   - **回归测试**：构造较大的代次（多表、多次发布，raw 合计至少几十 MB），断言以下三点。断言靠记录文件读取，不靠计时。
     - 首次保留之后，查询不再读取 raw 文件；
     - 依赖文件被改动后，查询报错；
     - 超时生效。
   - **ADR 0007**：实现说明随之更新，删掉"每次 pin/查询都校验 raw"的写法。
   - **风险记录**：本机每个代次都保留一份副本、没有清理，这一点不在本任务处理，但要在 `summary.md` 里写明每个代次占用多少磁盘、是硬链接还是复制。

# 不做
- 不改契约、模型和 schema（002 已定）。
- 不改各来源的解析器去自动填发布标识（各来源任务）；不写具体来源（Eurostat、IMF）的历史存档抓取。
- 不改 Eltiro 的代码。
