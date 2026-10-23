---
id: DATUMOJ-MAPLE-002
title: 身份与状态契约：快照顺序、解析上下文指纹、单调发布、观测值引用键、证据与管线状态；审核问题回归用例
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "src/datumoj/core/manifest.py", "src/datumoj/store/chunks.py", "tests/core/test_models.py", "tests/core/test_contracts.py", "tests/core/test_contracts_maple.py", "tests/core/test_manifest.py", "tests/index/test_fts.py", "tests/store/test_chunk_store.py", "schemas/*.schema.json", "docs/adr/0004-evidence-identity.md", "results/DATUMOJ-MAPLE-002/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example", "docs/PLAN.md", "docs/TASKS.md", "docs/plans/**", "docs/tasks/**", "docs/acceptance/**", "docs/references/**"]
protected: ["tests/fixtures/**", "schemas/registry/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_contracts_maple.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-002/summary.md 完成说明"]
  review: {by: human, checklist: ["身份定义是否覆盖计划 6.0 节 D-01–D-07、D-13", "指纹是否稳定且重复抓取不触发重解析", "发布是否单调、水位是否持久化", "v1 库迁移是否不丢快照", "现有调用方是否不受影响", "ADR 是否列出 10 个审核场景及负责任务", "全部已有测试是否通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
“哪一份证据”有了明确的身份：字节（blob）、一次观察（snapshot）、资源、解析产物、发布的当前版本、观测值引用各自怎么标识，写成契约、ADR 和失败用例。m0 其他任务都按这份契约修。

# 背景
开发线：契约。2026-10-23 采纳的 Codex 审核（计划 6.0 节）发现，现在多处把内容哈希（sha256）当成资源或引用的身份：解析状态表的键是 `(sha256, parser, version)`（`core/manifest.py`），`parse_pending`（`pipeline.py`）遇到同键就跳过，同字节的另一个资源、或 A→B→A 回到 A 时都不会处理；`SqliteChunkStore.put_document`（`store/chunks.py`）无条件把写入的版本设为 current，旧失败快照重试成功后会把 current 倒退；引用按“同 SHA 最近的成功快照”找（`analysis/tools.py` 的 `_snapshot`、`index/hybrid.py`），可能指到另一个 URL；观测值出处按 `obs_id` 全库回查（`store/observations.py` 的 `_provenance`），而 `obs_id` 只在表内唯一。本任务是 m0 的前提，牵连最广，由人审查。抓取重复内容时（同 URL、同字节）manifest 会记一条新的 ok 快照（见 `tests/core/test_runner.py` 的 `test_same_body_is_unchanged_without_304_and_ids_do_not_collide`），这类快照不应触发重解析。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-002/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. ADR `docs/adr/0004-evidence-identity.md`（状态“提议”，由人定）：定义 blob（字节，sha256）、snapshot（一次观察，`snapshot_id`）、资源（`source_id` + 去 Key 的 `resource_url`）、解析产物（parser、version、上下文指纹）、发布（每个 doc_key 的 current 与已发布水位）、观测值引用键 `(source_id, table_key, obs_id)`；写明每条规则对应审核的哪个问题（D-01–D-07、D-13），以及本地已有数据的迁移办法（重放到新目录，由 MAPLE-900 提供）。
2. **快照顺序**：`SnapshotOrder`（`fetched_at` + manifest 写入序号 `seq`，可比较，`fetched_at` 是快照记录的抓取时间，不是解析时间）；`Manifest` 协议加 `snapshot_order(snapshot_id) -> SnapshotOrder | None`。
3. **解析上下文指纹**：纯函数 `parse_context_fingerprint(snapshot, meta) -> str`，由 `source_id`、`resource_url`、`content_type` 和快照 meta（规范化 JSON）稳定算出；同 URL 同 meta 的重复抓取得到同一指纹。`ParseRecord` 加 `context_fingerprint`；`Manifest.get_parse` / `record_parse` 以 `(sha256, parser, version, context_fingerprint)` 为键。
4. `SqliteManifest` 按上一条实现：schema 版本升到 2；打开 v1 库时在一个事务里重建解析记录表，将主键改为 `(sha256, parser, version, context_fingerprint)`，不能仅新增列而保留三列唯一约束。保留全部快照行（含 seq）及旧解析记录，旧记录的指纹记为空串，不匹配新计算的上下文，下次解析时重做。迁移测试现场建 v1 库，验证旧记录与快照不丢失，同 SHA、同 parser/version、不同指纹的两条记录可同时写入并分别查回，重开库后仍成立。
5. **单调发布**：`ChunkStore.put_document(document, chunks, *, publish: bool = True)`，`publish=False` 只写入这个版本、不动 current；新方法 `publish_current(doc_key, snapshot_sha256, order: SnapshotOrder) -> bool`：只有 `order` 比该 doc_key 已发布水位新时才切换 current 并持久化水位，返回是否切换；`current_order(doc_key) -> SnapshotOrder | None`。`publish=True`（默认）对水位已知或无历史 current 的库保持兼容。`SqliteChunkStore` 同步实现并迁移 schema：旧库已有 current 时，必须从可确认的快照身份恢复其 `SnapshotOrder`；无法确认时持久化“待重放”状态，在有序重放完成并建立水位前拒绝普通发布（包括默认 `publish=True`），明确提示迁移要求，不能把空水位当成首次发布许可。这替代原“水位为空、第一次发布即覆盖”的约定；迁移测试必须覆盖已有较新 current、首次请求发布更旧快照及重开库后的行为，断言 current 不倒退。`PublishState` 枚举：`staged`、`published`、`failed`（给 MAPLE-107 用）。
6. **引用身份**：`Citation` 加 `snapshot_id: str | None = None`（新代码产生的引用必须填，由 MAPLE-404 接入）。`AmbiguousCitation` 异常或等价的结果状态，供“只给 SHA 且对应多个资源”时使用。
7. **观测值引用键**：`ObservationRef`（`source_id`、`table_key`、`obs_id`）及稳定的字符串形式（可解析回来，`table_key` 含分隔符时也不出错）。
8. **证据状态**：`EvidenceState` 枚举 `complete` / `partial` / `unavailable`；`ToolResult` 加可选的 `evidence_state` 和 `evidence_inputs`（输入的表、快照、过滤范围；JSON 可序列化），给 MAPLE-307 用。
9. **管线状态**：`GapState` 加 `not_indexed`（抓到了但还不能检索：没解析、解析失败、需要 OCR 或索引未建）；`PipelineStatus` 模型（可解析快照数、已解析、解析失败、needs_ocr、可检索块数、索引代次），`ToolResult` 加可选的 `pipeline`，给 MAPLE-503 用。
10. 假实现（`testing/fakes.py`）和合格测试（`testing/contract_suites.py`）同步覆盖上面每条新约束（指纹不同就不算已解析、发布单调、水位持久化、旧版本 `publish=False` 写入不改 current、`ObservationRef` 往返）；`SqliteManifest`、`SqliteChunkStore` 通过对应合格测试（`SqliteChunkStore` 的合格测试新建在 `tests/store/test_chunk_store.py`）。重新运行 `export_schemas` 并提交 `schemas/` 根目录的导出（不碰 `schemas/registry/`）。
11. ADR 里列出审核的 10 个合成复现场景和负责修复的任务（同字节不同资源、A→B→A：202；旧失败重试倒退：107；`obs_id` 串表：306；聚合无证据状态：307；同 SHA 引用指错：404；坏 raw 按旧哈希解析：109；先截 top-k 再过滤：502；PDF 块缺日期：203；`runs.jsonl` 坏尾行：108）。复现测试由各负责任务写在 `tests/regressions/test_audit_<任务号>.py`，**本任务不写**（调度器不接受 skip / xfail，问题没修之前测试无法通过）。（2026-10-23 第 1 次尝试后修订：原要求本任务写 xfail 回归用例，与调度器验收规则冲突，已取消。）
12. 新测试 `tests/core/test_contracts_maple.py` 覆盖上面每条约束；现有 `tests/core/test_models.py`、`tests/core/test_contracts.py`、`tests/core/test_manifest.py`、`tests/index/test_fts.py` 允许为本修订调整（只改受影响的断言，不删测试）。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改 `pipeline.py`、`analysis/tools.py`、`index/`、`store/observations.py` 的行为（由 202、107、404、306 等接入）。
- 不做 R2 状态代次和写入租约（MAPLE-001）。
