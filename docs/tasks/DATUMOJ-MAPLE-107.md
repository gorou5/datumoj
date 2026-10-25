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
scope: ["src/datumoj/pipeline.py", "src/datumoj/store/chunks.py", "src/datumoj/index/fts.py", "src/datumoj/index/vector.py", "src/datumoj/wiring.py", "tests/test_pipeline_publish.py", "tests/test_pipeline.py", "tests/test_pipeline_retry.py", "tests/regressions/test_audit_107.py", "src/datumoj/core/contracts.py", "src/datumoj/core/manifest.py", "src/datumoj/testing/fakes.py", "src/datumoj/testing/contract_suites.py", "tests/core/test_manifest.py", "tests/core/test_contracts_maple.py", "results/DATUMOJ-MAPLE-107/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline_publish.py
    - cmd: make check T=tests/test_pipeline_retry.py
    - cmd: make check T=tests/regressions/test_audit_107.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-107/summary.md 完成说明"]
  review: {by: human, checklist: ["current 是否只按快照顺序单调前进", "旧快照重试是否不影响索引", "每个故障注入点是否都不留下半新半旧", "恢复扫描是否恢复一致且只查询未完成的恢复工作", "全部已有测试是否通过", "是否越界"]}
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
1. `_dispatch` 以**整份快照**为发布单位：先完成所有文档的暂存（`publish=False`）、观测值和关键词等必需写入，再一次性激活全部满足单调水位条件的文档及其索引可见版本，`order` 取快照自身的 `SnapshotOrder`。不得在逐文档循环中调用 `publish_current` 造成部分切换；采用 ChunkStore 内的批量事务发布，若跨库无法统一事务，则必须有持久化、可验证的回滚协议，读取侧在发布 / 回滚完成前只看到完整的上一已发布状态。
2. 旧快照（顺序早于已发布水位）重试成功：只补写它自己的历史版本和解析记录，不改 current，关键词和向量索引不被换成旧内容；变化日志不产生反向变化。
3. 每个快照的发布状态（`staged` / `published` / `failed`）可查；状态写入与激活之间中断也必须可恢复，不能仅靠内存备份实现回滚。
4. **检索可见性**：`SqliteKeywordIndex.search` 和 `LanceVectorIndex.search` 必须排除未发布内容。索引按解析产物版本暂存，并根据同一个已发布 current / 发布标识过滤（过滤发生在 top-k 之前），保留上一已发布索引直至激活成功；或采用等价的写入隔离、失败清理及回滚协议。仅在 HybridRetriever 中丢弃找不到的块不够，直接索引查询也须满足约束。必需写入或激活失败时，整份快照记为 `failed`，全部文档的 current、水位和已发布关键词 / 向量结果保持不变；首次发布失败且无旧 current 时，该快照没有可见文档、关键词或向量命中，恢复清理不能把暂存内容当成 current 发布。
5. 故障注入测试 `tests/test_pipeline_publish.py`：在 ChunkStore 暂存后、关键词写入时、向量写入时、观测值写入时、发布前，以及多文档快照首个文档已执行激活写入而后续文档激活失败时注入异常。分别覆盖已有旧 current 与首次发布无 current，在发布前、每个故障后和下一次 `parse_pending` 恢复后，断言所有文档 current、水位、两种索引的直接检索结果及发布状态；批量事务内部首条更新后抛错必须整体回滚。另测同 doc_key / SHA 不同上下文重写和并发两次重试同一旧快照不倒退。向量的可选失败按要求 7(a) 断言，不与必需库失败混淆。
6. 现有 `tests/test_pipeline.py`、`tests/test_pipeline_retry.py` 中与新规则冲突的断言允许调整（写进 summary 逐条说明原因，不删测试）。审核回归测试 `tests/regressions/test_audit_107.py`：用合成数据离线复现审核场景——旧快照解析失败、新快照成功后再重试旧快照（旧快照这次成功），current 仍为新快照——断言修好以后的行为，必须通过（不能标 skip / xfail）。全部已有测试（`make check T=tests`）必须仍然通过。
7. **本次审核修订替代原人定方案 A 中“先逐文档切 current、再写关键词，失败后检索暂时不一致”的约定**，兼容边界如下：（a）必需的库仍只有 ChunkStore、关键词索引和观测值；向量保持可选语义，写失败记 vector missing、必需写入成功后照常发布，`tests/test_pipeline_vectors.py` 不改。部分写出的向量在激活前不可见，发布后只允许命中与 current 对应的版本，缺失部分按既有机制补写。（b）发布点改为要求 1 的快照批量激活，关键词必须在激活前就绪；变化事件只在整份快照激活成功后生成。（c）**契约补充，本任务允许**：保留 `Manifest.record_publish_state(snapshot_id, state: PublishState, error: str | None = None)`、`publish_state(snapshot_id) -> PublishState | None`、`snapshots_in_publish_state(state) -> list[str]`；`SqliteManifest` 持久化（schema 升到 4，v1–v3 都能迁移，迁移来的快照没有发布状态，记为 None，不参与恢复扫描）；`FakeManifest`、`ManifestSuite` 同步补用例。允许在 ChunkStore 协议 / 实现及对应假实现、合格测试中添加快照批量激活与必要回滚接口，保留原有单文档调用兼容；具体签名、调用方和迁移方法写进 summary。不改 `models.py` 和 KeywordIndex、VectorIndex 协议，索引实现通过兼容扩展完成版本隔离。（d）同 doc_key、同 SHA 的版本槽位被另一上下文重写时，也必须隔离已发布内容；只用 SHA 过滤不够，须区分指纹 / 发布版本，或可靠保存并恢复旧产物，失败后不能暴露重写的文本或引用。
8. **恢复扫描必须有界**：只定向查询尚未完成恢复的 staged / failed 快照，按对应解析记录 / 发布日志里的 doc_keys 清理未激活索引、恢复未完成的回滚或完成已提交激活的状态登记。正常失败返回前必须完成回滚；进程中断遗留工作在对外检索前恢复或由可见性过滤隔离，完成清理的 failed 项须有持久化完成标记，后续不重复处理。未提交激活的快照不能仅凭暂存条目被标为 published；下一次正常重试仍按要求 1 激活。不得遍历全部来源的全部快照、读 chunk 全表（`iter_chunks()`）或重建全部 current 文档的关键词；本任务允许在 Manifest 及实现、假实现、合格测试中补充待恢复工作查询和完成标记；查询必须排除已清理的 failed 项，不能先列出全部历史失败再过滤，开销只与未完成的恢复工作相关。新增测试断言没有待恢复工作时不调用 `iter_chunks`、不写索引、不写发布状态。
9. 为有界恢复保留契约方法 `Manifest.get_snapshot(snapshot_id) -> Snapshot | None`（按主键取一条，未知返回 None；`SqliteManifest` 按 `snapshot_id` 唯一索引查询，`FakeManifest`、`ManifestSuite` 同步）。恢复扫描可通过 `get_snapshot` → `snapshot_meta` 算指纹 → 按注册表 kind 和 content_type 选解析器 → `get_parse(..., 指纹)` 取 doc_keys；若解析记录尚未成功写入，必须从激活前持久化的发布日志取得全部目标文档与旧版本，不能遗漏半途失败的多文档快照。

# 不做
- 不做 R2 上传和状态代次（MAPLE-102）。
- 不做向量追加性能优化与全量代际重建（MAPLE-111）；本任务只调整发布所需的版本隔离。
- 不改变向量写入的可选语义；本任务允许为发布隔离增加索引版本 / 暂存，不在此实现全量重建代际。
