---
id: DATUMOJ-TACHIBANA-402
title: 目录记录带快照引用：search_catalog 的出处能用 cite 还原
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-001"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/ckan_meta.py", "src/datumoj/wiring.py", "src/datumoj/analysis/tools.py", "tests/parsers/test_ckan_meta_snapshot.py", "tests/analysis/test_catalog_citation.py", "results/DATUMOJ-TACHIBANA-402/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/analysis/test_tools.py", "tests/store/test_catalog.py", "tests/integration/test_full_eval.py", "tests/integration/test_stats_e2e.py"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_ckan_meta_snapshot.py
    - cmd: make check T=tests/analysis/test_catalog_citation.py
    - cmd: make check T=tests/analysis
    - cmd: make check T=tests/store/test_catalog.py
    - cmd: make check T=tests/integration
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-402/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
目录检索（`search_catalog`）的每条结果都带出处（来源、URL、抓取时间、快照哈希、定位），并且能用 `cite` 还原到那次快照里的原始目录记录。

# 背景
开发线：D 目录线。IVY 验收的遗留缺口（计划 6.5 节）：目录结果还原不到快照。CKAN 目录解析器在 `parsers/ckan_meta.py`（接线层 `wiring._CkanPages` 包了一层）；目录库 `store/catalog.py` 以 JSON 保存整条 `Dataset`，新字段会随之保存；`search_catalog` 和 `cite` 在 `analysis/tools.py`。契约 001 已给 `Dataset` 加了 `snapshot_sha256`、`locator`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-402/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. CKAN 目录解析器给每条 `Dataset` 填 `snapshot_sha256`（目录分页快照）和 `locator`（`entry=<在 results 数组里的下标>`），并提高解析器版本号，使 `datumoj parse` 会重新解析已有的目录分页快照、补上引用（不需要重新抓取）。
2. `search_catalog` 的每条结果加 `citation`：按 `snapshot_sha256` 从 manifest 找快照，用契约的 Citation 规则生成（时间口径：有 `modified_at` 用发布日，否则 `no_data_date`；带许可和 `usage_scope`）。没有快照引用的旧记录 `citation` 为 null，并在 `warnings` 里说明“该记录缺少快照引用，重新解析后可还原”。
3. `cite(snapshot_sha256, locator)` 支持 `entry=N`：快照是 CKAN `package_search` 的 JSON 时，返回 `result.results[N]` 的原文（包在 `SourceText` 里）；下标越界或快照不是该格式时返回清楚的错误。
4. e-Stat 表的目录记录（301 产出，locator 为单元格）沿用 `cite` 现有的单元格还原，不需要额外处理；测试里用一条手工构造的 Dataset 验证 `search_catalog` 的 citation 对这种 locator 也正确。
5. 新测试覆盖每条，用 `tests/fixtures/ckan_egov/` 的真实分页样本；`tests/analysis`、目录解析器原有的测试 `tests/store/test_catalog.py`、`tests/integration` 必须仍然通过。

# 不做
- 不改目录库的表结构和检索排序。
- 不改 MCP 工具的名字和参数（输出多一个字段）。
