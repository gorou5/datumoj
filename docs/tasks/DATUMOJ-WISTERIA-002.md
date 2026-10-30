---
id: DATUMOJ-WISTERIA-002
title: 观测值按发布时间查询的契约：发布标识、截至查询、generation_token 固定代次读取
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-WISTERIA-001"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/models.py", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/testing/**", "schemas/*.schema.json", "docs/adr/0007-observation-releases.md", "tests/core/test_models.py", "tests/core/test_contracts.py", "tests/regressions/test_audit_wisteria_002.py", "results/DATUMOJ-WISTERIA-002/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/plans/**", "sources/**", "src/datumoj/rikolto/store/**", "src/datumoj/rikolto/analysis/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_wisteria_002.py
    - cmd: make check T=tests/core
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-WISTERIA-002/summary.md 完成说明"]
  review: {by: human, checklist: ["数据代次和来源发布是否分清", "发布标识是否沿用 306 的引用键和 307 的证据状态、没有另起一套", "token 无效或代次不可用时是否一律报错、不回退到最新", "新增字段是否可选、现有解析器和数据不受影响", "截至查询的语义（同一发布日多次、没有发布标识的旧数据）是否写清", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
定下"观测值按发布时间查询"的契约，供 301 实现、Eltiro 执行器（804）使用：观测值带**来源发布标识**，同一序列的历次发布都保留并能"截至某次发布"查询；一次运行用 `generation_token` 固定在一个已发布的数据代次上读，不会读到运行中出的新代次。只定契约，不改存储和查询的实现。

# 背景
开发线：契约。试点 V07（希腊赤字修订链）要比较同一指标的多次发布；Eltiro 执行时要固定数据代次（`docs/plans/0.4-wisteria.md` 第 6.5、6.9 节）。两件事要分清：
- **数据代次**：Rikolto 自己发布的状态代次（MAPLE 的 `StateGeneration`、107 的发布状态）；
- **来源发布**：来源方的一次发表（如 Eurostat 某次 EDP 通报），一个数据代次里可以有同一序列的多次来源发布。

现有基础：MAPLE 306 的观测值引用键 `ObservationRef(source_id, table_key, obs_id)`、307 的证据状态 `EvidenceState`、`Observation.snapshot_sha256`、`SnapshotOrder`。本任务在它们之上加，不另起一套。

本版本在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。Rikolto 的模块在 `src/datumoj/rikolto/`。
- 本任务就是改契约：只动 `rikolto/core/models.py`、`rikolto/core/contracts.py` 和 `rikolto/testing/`；存储、查询、解析器的实现不改（301 做）。
- 回归测试写在 `tests/regressions/test_audit_wisteria_002.py`；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- `tests/fixtures/` 只读。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **发布标识**：`Observation` 加可选的 `release_id`（来源方的发布标识）和 `released_at`（发布日期）；缺省为空，现有解析器和已有数据不受影响。没有发布标识的观测值按"抓取快照即一次发布"处理（用 `snapshot_sha256` 和 `fetched_at`），规则写进 ADR。
2. **完整身份**：定义 `ObservationIdentity` = `ObservationRef` + `snapshot_sha256` + `release_id`（可空）；`str` / `parse` 形式照 `ObservationRef`（紧凑 JSON 数组）。只按 `obs_id` 关联或去重的用法在契约里写明禁止。
3. **generation_token**：定义 `GenerationToken`：绑定数据代次号和该代次清单的内容哈希，不透明字符串形式可往返解析；格式里不含路径、主机或凭证。
4. **查询契约**（`core/contracts.py`）：只读查询接口加两个可选参数：`generation_token`（固定代次读；token 无效、代次不存在或已不可用一律报错，**不回退到最新代次**）和 `as_of_release`（截至某个发布日期或发布标识，取每条观测值在此之前的最近一次发布）；两者可以同时用。返回结果里每行带 `ObservationIdentity` 和所读的代次号。报错用明确的错误类型，不吞掉。
5. **证据状态**：截至查询得到的聚合沿用 307 的 `EvidenceState`，输入范围里写明截至条件。
6. **假实现和契约测试**：`rikolto/testing/` 的假实现支持上面的语义；契约测试套件覆盖：同一观测值多次发布、截至查询取对版本、跨表同 `obs_id` 不串、运行中出新代次时固定 token 仍读旧代次、token 无效或代次不可用时报错、没有发布标识的旧数据按快照处理。
7. **Schema**：重新导出 `schemas/`，和仓库一致（测试断言）。
8. **ADR 0007**（`docs/adr/0007-observation-releases.md`）：数据代次与来源发布的区别、发布标识的来源和缺省规则、截至查询的语义（同一天多次发布按 `SnapshotOrder` 排序）、token 的格式和失效规则。ADR 0006 留给 001。
9. 回归测试 `test_audit_wisteria_002.py` 覆盖要求 1–6 的关键断言；`make check T=tests` 全部通过。

# 不做
- 不改观测值库、`query_sql`、MCP 工具的实现（301）。
- 不改各来源解析器去填发布标识（301 和各来源任务）。
- 不导入历史发布存档（301）。
- 不碰 Eltiro 的契约（001）。
