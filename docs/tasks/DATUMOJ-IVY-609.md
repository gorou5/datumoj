---
id: DATUMOJ-IVY-609
title: 修复：已成功下载的目录资源要标为“已抓取正文”
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/pipeline.py", "tests/test_pipeline_content_state.py", "results/DATUMOJ-IVY-609/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_pipeline.py", "tests/test_pipeline_retry.py", "tests/test_pipeline_vectors.py"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline_content_state.py
    - cmd: make check T=tests/test_pipeline.py
    - cmd: make check T=tests/test_pipeline_retry.py
    - cmd: make check T=tests/test_pipeline_vectors.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-609/summary.md 完成说明"]
  review: {by: other_family, checklist: ["成功下载但解析器没产出块或观测值的资源是否标为 content_fetched", "资源快照先于目录分页解析时，状态是否在目录写入后补上", "状态是否只升不降（observations_loaded 不会被改回 content_fetched）", "失败的抓取是否不会被标为已抓取", "是否用真实 SqliteCatalogStore 验证", "原有流水线测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
目录（CKAN）里的资源文件只要成功下载过，目录库里的状态就是“已抓取正文”（`content_fetched`）；解析成观测值的是 `observations_loaded`；只有元数据的才是 `catalog_only`。

# 背景
开发线：F 入口线（修复 DATUMOJ-IVY-601）。M2 真实抓取验收（H4）抓了 e-Gov 目录 200 个数据集、2,130 个资源快照并全部解析成功，但目录库里 3,187 个资源**全部**仍是 `catalog_only`。原因（`src/datumoj/pipeline.py` 的 `_process`）：
1. 只有解析结果里有 `observations` 或 `chunks` 时才调用 `set_content_state`；CKAN 资源文件（CSV、PDF 等）目前没有解析器把它们转成块或观测值，接线层的适配器对 `role=resource` 的快照返回空结果，于是“下载成功”从未被记录。
2. 资源快照可能比目录分页（产生 Resource 记录的那份快照）先被解析，那时目录库里还没有这条资源，`set_content_state` 更新 0 行，状态就丢了。

目录库的 `upsert` 不会覆盖已有状态（`ON CONFLICT ... DO UPDATE` 不改 `state`），不需要改。计划见 `docs/PLAN.md`；测试全部离线，不能改依赖、契约和目录库。

# 要求
1. 成功解析的快照（无论解析结果是否为空），如果它的 `resource_url` 是目录里的某个资源，状态至少提升到 `content_fetched`；产出观测值的仍为 `observations_loaded`。
2. **状态只升不降**：已是 `observations_loaded` 的资源不能被改回 `content_fetched`（可用契约里的 `max_state`，当前状态可通过 `CatalogStore.resources_for(dataset_key)` 读到；资源快照的 meta 里有 `dataset_id`，目录分页的结果里有 Resource 记录）。
3. 补记：每次把 Resource 写入目录后，对这些资源的 URL 查 manifest（`Manifest.last_success`），已经有成功快照的，按第 1、2 条补上状态，这样解析顺序不影响结果。
4. 抓取失败（没有成功快照）的资源保持 `catalog_only`。
4a. 已有数据要能补上：`reindex(deps, source_id)`（即 `datumoj index --rebuild`）重建时，对目录里该来源的全部资源按第 1–3 条重新核对一遍状态（只升不降），不需要重新解析快照。测试覆盖这一点。
5. 原有 `tests/test_pipeline.py`、`tests/test_pipeline_retry.py`、`tests/test_pipeline_vectors.py` 已列为 protected，必须全部通过。
6. 新测试 `tests/test_pipeline_content_state.py`：用真实的 `SqliteCatalogStore`、`SqliteManifest`、`FileRawStore`（`tmp_path` 里）加 `tests/fixtures/ckan_egov/` 的分页样本和资源样本：资源先于分页解析、分页先于资源解析两种顺序都得到 `content_fetched`；失败的资源保持 `catalog_only`；已是 `observations_loaded` 的不被降级；重复解析不改变结果。

# 不做
- 不改目录库、CKAN 采集器和接线适配器、契约；不新增资源文件的正文解析。
