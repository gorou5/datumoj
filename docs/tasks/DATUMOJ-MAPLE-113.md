---
id: DATUMOJ-MAPLE-113
title: 升级后的发布水位（D-02 补漏）：v1 库迁移后 current 不被旧快照倒退
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-107"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/pipeline.py", "src/datumoj/store/chunks.py", "tests/store/test_chunk_store.py", "tests/test_pipeline_publish.py", "tests/regressions/test_audit_113.py", "results/DATUMOJ-MAPLE-113/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_113.py
    - cmd: make check T=tests/store/test_chunk_store.py
    - cmd: make check T=tests/test_pipeline_publish.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-113/summary.md 完成说明"]
  review: {by: human, checklist: ["迁移来的 current 是否在第一次发布前补上水位", "补水位是否宁可偏大也不倒退", "manifest 缺记录时是否不切换 current", "没有改契约", "全部已有测试是否通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
从 v1 升级上来的 ChunkStore，已有的当前版本在第一次重新发布时不会被较旧的快照替换掉；升级前后 current 只按快照顺序向前走。

# 背景
开发线：A 云端运行与运行基础。审核 D-02 由 MAPLE-107 修复：`ChunkStore.publish_current` 按 `published_orders` 里的水位比较快照顺序，只让更新的快照成为 current。但 `store/chunks.py` 的 `_init_schema` 把 v1 库升到 v2 时，只建了空的 `published_orders` 表，没有给已有的 `current_versions` 补水位。于是升级后每个已有文档的 `current_order` 都是 None，第一次 `publish_current` 不论快照新旧都会成功：例如解析器版本提高后重新解析较旧的快照，会把 current 退回旧版本。PR #21 的审查意见（MAPLE-002 第 46 行）提过这个问题，107 合并时没有处理。MAPLE 的 H3（本机数据副本迁移成第一个代次）正好会遇到这种情况。

已有的、本任务可以直接用的接口（都已在契约里）：`Manifest.snapshot_order(snapshot_id)`、`Manifest.snapshots_by_sha256(sha256)`、`Manifest.get_snapshot`、`ChunkStore.current_order`、`ChunkStore.publish_current`、`ChunkStore.get_document`。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节 D-02），总计划 `docs/PLAN.md`。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。本任务不改契约；确实需要时写进 `results/DATUMOJ-MAPLE-113/summary.md` 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_113.py`，断言修好以后的正确行为；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`，不写仓库里受 git 跟踪的路径。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- `tests/fixtures/` 下已有的样本只读。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 发布前补水位：在管线发布一个文档（`pipeline.py` 的 `_publish_document`）之前，如果该文档已经有 current（`get_document(doc_key)` 不为空）但没有水位（`current_order(doc_key)` 为 None），先为当前版本补上水位，再按现有规则比较本次快照。
2. 水位取法：在 `manifest.snapshots_by_sha256(当前版本的 snapshot_sha256)` 中，取与本次快照同一来源、同一 `resource_url` 的快照，水位取其中最大的 `snapshot_order`。宁可偏大，也不能让 current 倒退。用现有的 `publish_current(doc_key, 当前版本的 sha, 该顺序)` 写入，不新增接口。
3. 找不到对应快照（manifest 缺记录）时：只有本次快照是同一资源已知快照中顺序最新的，才切换 current；否则不切换，只补它自己的历史版本，并写一条警告日志（含 doc_key）。
4. 升级时不在 `_init_schema` 里猜水位（ChunkStore 拿不到 manifest）；`_init_schema` 的现有迁移行为不变。
5. 回归测试 `tests/regressions/test_audit_113.py`：在 `tmp_path` 里手工建一个 v1 结构的 ChunkStore，current 是较新的快照 B，manifest 里有较旧的 A 和 B；
   - 重新解析并发布 A（如解析器版本提高）后，current 仍是 B；
   - 之后发布更新的 C，current 变成 C；
   - manifest 缺 B 的记录时，A 不切换 current，C（同资源最新）切换。
6. `make check T=tests` 全部通过；与新规则冲突的已有断言允许调整，在 summary 里逐条说明原因，不删测试。

# 不做
- 不改观测值的写入和水位（MAPLE-114）。
- 不加单独的迁移命令，也不改 `diagnose`。
- 不改契约和 `published_orders` 的表结构。
