---
id: DATUMOJ-MAPLE-107
title: 发布状态与单调 current（D-02）：暂存、发布点、失败与恢复扫描
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-202", "DATUMOJ-MAPLE-404", "DATUMOJ-MAPLE-306"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/pipeline.py", "src/datumoj/store/chunks.py", "src/datumoj/index/fts.py", "src/datumoj/wiring.py", "tests/test_pipeline_publish.py", "tests/test_pipeline.py", "tests/test_pipeline_retry.py", "tests/regressions/test_audit_107.py", "src/datumoj/core/contracts.py", "src/datumoj/core/manifest.py", "src/datumoj/testing/fakes.py", "src/datumoj/testing/contract_suites.py", "tests/core/test_manifest.py", "tests/core/test_contracts_maple.py", "results/DATUMOJ-MAPLE-107/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline_publish.py
    - cmd: make check T=tests/test_pipeline_retry.py
    - cmd: make check T=tests/regressions/test_audit_107.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-107/summary.md 完成说明"]
  review: {by: human, checklist: ["current 是否只按快照顺序单调前进", "旧快照重试是否不影响索引", "每个故障注入点是否都不留下半新半旧", "恢复扫描是否能收拾一致且只处理 staged（开销与 staged 数量相关）", "全部已有测试是否通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
旧快照失败后重试成功，不会再把文档的当前版本倒退；一次解析写多个库时，中途失败不会让检索看到一半新一半旧的结果，下次运行能把状态收拾一致。

# 背景
开发线：A 云端运行与运行基础。审核 D-02：`_dispatch`（`pipeline.py`）先更新 ChunkStore 的 current，再删旧关键词、加新关键词、写向量和 Parquet，解析状态最后才记；current 的 upsert 不比较快照顺序。契约（MAPLE-002）已提供 `SnapshotOrder`、`Manifest.snapshot_order`、`ChunkStore.put_document(..., publish=False)`、`publish_current`、`current_order`、`PublishState`。MAPLE-202 已让每个成功快照都登记并选 current。这里的发布点以后与 R2 状态代次（MAPLE-102）共用。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-107/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 解析结果先以 `publish=False` 写入 ChunkStore（暂存），关键词、向量、观测值等必需的库写完后，才调用 `publish_current(doc_key, sha, order)` 切换 current；`order` 取快照自身的 `SnapshotOrder`。
2. 旧快照（顺序早于已发布水位）重试成功：只补写它自己的历史版本和解析记录，不改 current，关键词和向量索引不被换成旧内容；变化日志不产生反向变化。
3. 每个快照的发布状态（`staged` / `published` / `failed`）可查（存在 manifest 或 ChunkStore 里，由你按 ADR 0004 选，写进 summary）。
4. 中途失败（任一必需库写入抛错）：该快照记为 `failed`，current 和已发布的索引不变；下一次 `parse_pending` 开始时做**恢复扫描**：发现 `staged` 但未发布的、或关键词索引与 current 不一致的 doc_key，按 current 重建它的关键词条目（向量缺失沿用现有的 vector missing 记录补写）。
5. 故障注入测试 `tests/test_pipeline_publish.py`：在 ChunkStore 暂存后、关键词写入时、向量写入时、观测值写入时、发布前各注入一次异常，断言 current、关键词检索结果、发布状态都符合上面的规则，且下一次运行恢复一致；另有并发两次重试同一旧快照不产生倒退的用例。
6. 现有 `tests/test_pipeline.py`、`tests/test_pipeline_retry.py` 中与新规则冲突的断言允许调整（写进 summary 逐条说明原因，不删测试）。审核回归测试 `tests/regressions/test_audit_107.py`：用合成数据离线复现审核场景——旧快照解析失败、新快照成功后再重试旧快照（旧快照这次成功），current 仍为新快照——断言修好以后的行为，必须通过（不能标 skip / xfail）。全部已有测试（`make check T=tests`）必须仍然通过。
7. （2026-10-23 两次尝试后由人定的方案 A，**以本条为准**，与上面各条冲突处按本条）：（a）**必需的库**只有 ChunkStore、关键词索引和观测值；向量保持现在的可选语义（写失败记 vector missing、照常发布，`tests/test_pipeline_vectors.py` 不改）。（b）**唯一发布点**是 `ChunkStore.publish_current`：先写观测值和暂存文档（`publish=False`），再 `publish_current`，之后写关键词索引，关键词写完才把快照标为 `published`；关键词写入失败时快照保持 `staged`，下一次 `parse_pending` 开始时的恢复扫描对 `staged` 且是 current 的文档按当前版本重建关键词条目、补写向量缺失，然后标 `published`；`staged` 但不是 current 的（旧快照重试）直接标 `published`（只补历史）。观测值写入失败时不发布，标 `failed`。故障注入测试按这个顺序覆盖。失败到恢复之间关键词检索可能仍是旧内容，引用始终按 current，不会指错；这一点写进 summary。（c）**契约补充，本任务允许**：`Manifest` 加 `record_publish_state(snapshot_id, state: PublishState, error: str | None = None)`、`publish_state(snapshot_id) -> PublishState | None`、`snapshots_in_publish_state(state) -> list[str]`；`SqliteManifest` 持久化（schema 升到 4，v1–v3 都能迁移，迁移来的快照没有发布状态，记为 None，不参与恢复扫描）；`FakeManifest`、`ManifestSuite` 同步补用例。不改 `models.py`（`PublishState` 已有）和 ChunkStore、KeywordIndex、VectorIndex 协议。（d）同 doc_key、同 SHA 的版本槽位被另一上下文重写（202 的复用条件不满足时）且它正是 current：视为同一份字节的重写，重写后由本次发布或恢复扫描重建关键词条目；写进 summary。
8. （2026-10-23 人审打回后补充）**恢复扫描必须有界**：只处理 `snapshots_in_publish_state(staged)` 里的快照，按它们解析记录的 `doc_keys`，对其中 current 仍是该快照版本的文档重建关键词条目、补写向量缺失，然后标 `published`；不再是 current 的直接标 `published`。不得每次 `parse_pending` 遍历全部来源的全部快照、读 chunk 全表（`iter_chunks()`）或重建全部 current 文档的关键词；恢复开销只与 staged 数量相关。新增测试断言：没有 staged 快照时恢复扫描不调用 `iter_chunks`、不写关键词索引、不写 manifest 发布状态。上一版其余做法保留：先写观测值和暂存文档、再 `publish_current`、再写关键词；旧快照重试不动 current；观测值失败标 `failed`；变化事件在发布时生成；manifest 发布状态接口与 v4 迁移；故障注入测试。

# 不做
- 不做 R2 上传和状态代次（MAPLE-102）。
- 不改向量索引的追加和重建方式（MAPLE-111）。
- 不给关键词或向量索引加暂存（人定方案 A）。
