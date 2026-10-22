---
id: DATUMOJ-TACHIBANA-001
title: 契约修订：变化事件、文档类型、快照父子、目录快照引用、失败分类、RawStore 去掉本机路径
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "src/datumoj/core/manifest.py", "tests/core/test_models.py", "tests/core/test_contracts.py", "tests/core/test_contracts_tachibana.py", "schemas/*.schema.json", "results/DATUMOJ-TACHIBANA-001/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "schemas/registry/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_contracts_tachibana.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-001/summary.md 完成说明"]
  review: {by: human, checklist: ["字段和校验是否覆盖计划 6.1 节", "event_id 是否稳定且不含易变字段", "失败分类表是否逐条有测试", "去掉 path_for 后是否没有调用方被破坏", "parse_stats 是否按抓取时间统计且只取最新解析记录", "全部已有测试是否通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
TACHIBANA 各线要用的类型和接口先定下来：变化事件和变化日志、文档类型、快照之间的父子关系、目录数据集的快照引用、抓取失败分类；`RawStore` 不再暴露本机路径，为网络存储（R2）做准备。

# 背景
开发线：契约。这是 TACHIBANA 并行开发的前提，牵连最广，由人审查（计划 6.1 节）。`ChangeEvent` 给变化检测（601）、报告（602）、MCP（603）用；失败分类给监控（102）用；`Dataset` 快照引用给目录（402）和 e-Stat（301）用；`RawStore` 去掉 `path_for` 给 S3 实现（103）用。现有协议和假实现在 `contracts.py`、`testing/fakes.py`、`testing/contract_suites.py`；`SqliteManifest` 在 `core/manifest.py`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-001/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `DocKind` 枚举：`law`、`html_page`、`pdf`、`dataset`、`bulletin`。`Document` 加 `kind: DocKind | None = None`（IVY 已入库的数据为 None；本版本新写的解析器必须填）。
2. `Dataset` 加 `snapshot_sha256: Sha256 | None = None` 和 `locator: Locator | None = None`，表示这条目录记录来自哪一次快照的哪个位置；有 `locator` 时 `snapshot_sha256` 必填。
3. `ChangeKind` 枚举：`added`、`modified`、`removed`、`fetch_failed`、`parse_failed`。`ChangeEvent` 模型：`event_id`、`source_id`、`kind`、`doc_key`（可空，属于该来源）、`resource_url`（可空，已去 Key）、`old_sha256`、`new_sha256`、`detected_at`（带时区）、`summary`（一行，必填）、`locator`（可空）。校验：`doc_key` 和 `resource_url` 至少一个；`added` 必须有 `new_sha256` 且没有 `old_sha256`；`removed` 必须有 `old_sha256`；`modified` 两个都有且不相等。`event_id` 由 `source_id`、`kind`、`doc_key`、`resource_url`、`old_sha256`、`new_sha256` 稳定生成（同一变化两次生成得到同一个 ID），提供 `ChangeEvent.make(...)` 构造函数。
4. `ChangeLog` 协议：`append(events)`（按 `event_id` 幂等，重复写不产生重复行）；`list(since: datetime | None = None, until: datetime | None = None, source_ids=None, kinds=None, limit=1000)`，按 `detected_at` 升序。`FakeChangeLog` 和 `ChangeLogSuite`（覆盖幂等、过滤、排序、时间边界）。
5. `FailureKind` 枚举：`source_unavailable`、`rate_limited`、`auth_failed`、`format_changed`、`policy`、`other`；纯函数 `classify_failure(outcome: FetchOutcome, http_status: int | None) -> FailureClass | None`（`FailureClass` 含 `kind` 和 `retryable`）。规则：`ok`、`not_modified` 返回 None；`network_error`、HTTP 5xx、408 → `source_unavailable` 可重试；404、410 → `source_unavailable` 不可重试；429 → `rate_limited` 可重试；401、403、407 → `auth_failed` 不可重试；`spa_shell`、`empty_body` → `format_changed` 不可重试；`too_large`、`blocked_by_robots` → `policy` 不可重试；其他 4xx 和未知 → `other` 不可重试。解析失败另给 `classify_parse_failure() -> FailureClass(format_changed, retryable=False)`。规则写成表，测试逐条覆盖。
6. 快照父子关系约定：`META_PARENT_URL = "parent_url"`、`META_PARENT_SHA256 = "parent_sha256"`（写在 `FetchTask.meta` 里，随快照进 manifest；解析器把它们原样放进 `Document.metadata`）。提供 `parent_ref(meta) -> tuple[str, str] | None`：两个键都在且 sha256 格式正确时返回，否则 None；URL 必须已去 Key。
7. `Manifest` 协议加 `parse_stats(source_id: str, since: datetime | None = None) -> dict[ParseStatus, int]`：按**快照抓取时间** `fetched_at >= since` 统计该来源快照的最新解析状态（同一 sha256 多个解析器版本时取最新记录）。在 `SqliteManifest`（`core/manifest.py`）里实现，`FakeManifest` 和 `ManifestSuite` 同步补上。只加这一个方法，`manifest.py` 的其他行为不变。
8. `RawStore` 协议去掉 `path_for`（本机路径不属于协议，S3 实现给不出）；`FileRawStore` 保留它作为自己的方法，现有调用不受影响。`RawStoreSuite` 和 `FakeRawStore` 同步，不再依赖 `path_for`。
9. 重新运行 `export_schemas` 并提交 `schemas/` 根目录的导出结果（不要碰 `schemas/registry/`）。
10. 新测试 `tests/core/test_contracts_tachibana.py` 覆盖上面每一条约束；现有 `tests/core/test_models.py`、`tests/core/test_contracts.py` 允许为本修订调整（只改受影响的断言，不删测试）。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不写变化检测、监控、S3 存储等真实实现（只有类型、协议、假实现、合格测试，以及 `SqliteManifest.parse_stats`）。
- 不加 KKJ 用的 `Redaction`（KKJ 已移出本版本）。
