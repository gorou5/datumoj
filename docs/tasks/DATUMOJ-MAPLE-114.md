---
id: DATUMOJ-MAPLE-114
title: 观测值表的发布水位（D-02 补漏）：旧快照重试不覆盖较新的表
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-113"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/store/observations.py", "src/datumoj/pipeline.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/fakes.py", "src/datumoj/testing/contract_suites.py", "tests/store/test_observations.py", "tests/core/test_contracts_maple.py", "tests/test_pipeline_publish.py", "tests/regressions/test_audit_114.py", "results/DATUMOJ-MAPLE-114/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example", "src/datumoj/core/state.py"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_114.py
    - cmd: make check T=tests/store/test_observations.py
    - cmd: make check T=tests/test_pipeline_publish.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-114/summary.md 完成说明"]
  review: {by: human, checklist: ["旧快照重试是否不再覆盖较新的表", "同一快照重写是否仍然允许", "水位是否和表在同一次原子替换里写入", "迁移来的表是否按行里的快照补水位", "状态代次复制和恢复后水位是否还在", "全部已有测试是否通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
较旧的快照解析成功后（包括失败后重试成功），不会用旧数据替换已经由较新快照写入的观测值表；`query_sql` 查到的始终是该表已知最新快照的数据。

# 背景
开发线：A 云端运行与运行基础。MAPLE-107 把观测值定为必需的库：在 `publish_current` 之前写入，写失败则不发布。但 `pipeline.py` 的 `_dispatch` 对每张表无条件调用 `ObservationStore.write_table`，`store/observations.py` 整体替换 `(source_id, table_key)` 对应的 Parquet 文件，不比较快照顺序。所以 107 要求 2 的场景（旧快照重试成功只补历史、不动 current）对观测值不成立：旧快照的表会覆盖新快照的表，文档 current 是新的，观测值却是旧的。PR #21 的审查意见（MAPLE-107 第 48 行）提过观测值缺少发布隔离，107 合并时没有处理。

观测值没有历史版本，每张表只有一份，所以这里用“表级水位”：记下写入这张表的快照顺序，旧的不写。

状态代次（`src/datumoj/core/state.py`）只复制 `parquet/<来源>/<32 位哈希>.parquet` 和 `observations.duckdb`，并按文件名校验。水位因此写进该表 Parquet 文件自身的元数据（key-value metadata），和表一起原子替换、一起复制和恢复，不新增文件，也不改 `state.py`。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节 D-02），总计划 `docs/PLAN.md`。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除要求 4 明确允许的契约补充外，不改契约；需要时写进 summary 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_114.py`，断言修好以后的正确行为；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`，不写仓库里受 git 跟踪的路径。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- `tests/fixtures/` 下已有的样本只读。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. **表级水位**：每张表 `(source_id, table_key)` 记录写入它的快照的 `SnapshotOrder`，存在该表 Parquet 文件的元数据里，和表数据在同一次 `os.replace` 中生效。崩溃后只能看到“旧表 + 旧水位”或“新表 + 新水位”。
2. **写入规则**：本次快照顺序早于该表水位时不写，旧快照重试只补文档自己的历史；等于水位时允许重写（同一快照用新解析器重写，同 107 要求 7(d)）；晚于水位或表还不存在时写入并更新水位。空列表（删表）同样受这条规则约束。
3. **没有水位的表**（升级前写入的）：先按表中行的 `snapshot_sha256`，经 `manifest.snapshots_by_sha256` 取同来源快照中最大的 `snapshot_order` 作为水位，再按要求 2 比较；取不到时按 MAPLE-113 要求 3 的同样规则处理（本次快照是同资源已知最新才写，否则不写并告警）。
4. **契约补充，本任务允许**：`ObservationStore.write_table(source_id, table_key, observations, order: SnapshotOrder | None = None) -> bool`。不传 `order` 时保持现在的无条件替换（兼容现有调用），返回值表示是否写入；新增 `table_order(source_id, table_key) -> SnapshotOrder | None` 读取水位。`DuckDBObservationStore`、`FakeObservationStore`、`ObservationStoreSuite` 同步，契约套件补上述规则的用例。不改 `models.py`。
5. **管线**：`_dispatch` 写观测值时传本次快照的顺序。被水位挡下的表不算写入失败，快照照常按 107 的流程发布或只补历史。
6. **发布隔离**：按 107 方案 A 的先例，观测值仍在 `publish_current` 之前写、不做暂存。文档发布失败时，已写入的较新观测值保持可查（来自同一个解析成功的快照，出处指向该快照）。这个窗口在 summary 里写明。
7. **回归测试** `tests/regressions/test_audit_114.py`：快照 A 较旧、B 较新，B 先成功，A 失败后重试成功，`query_sql` 返回 B 的值；同一快照重写生效；手工写一张没有水位的旧表（行属于 B）后重试 A，仍是 B 的值；状态代次 push 再 restore 后 `table_order` 不变。
8. `make check T=tests` 全部通过；与新规则冲突的已有断言允许调整，在 summary 里逐条说明原因，不删测试。

# 不做
- 不给观测值加历史版本或按发布时间查询（0.4 WISTERIA 的 002 / 301）。
- 不做观测值暂存和恢复扫描。
- 不改 `core/state.py` 的复制范围和文件名校验。
- 不改 ChunkStore 的水位（MAPLE-113）。
