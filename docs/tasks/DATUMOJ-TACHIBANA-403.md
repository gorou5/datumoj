---
id: DATUMOJ-TACHIBANA-403
title: 注册层描述导出：datumoj registry export 生成 catalog/*.json
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-103"]
runner: codex
model: null
tier: low
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/registry_export.py", "src/datumoj/cli.py", "catalog/**", "tests/core/test_registry_export.py", "docs/interoperability.md", "results/DATUMOJ-TACHIBANA-403/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", "docs/PLAN.md", "docs/TASKS.md", "docs/plans/**", "docs/tasks/**", "docs/acceptance/**", "docs/references/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "schemas/registry/**", "tests/validate_schemas.py", "sources/**", "tests/test_cli.py"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_registry_export.py
    - cmd: make check T=tests/test_cli.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-403/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
团队描述标准（`schemas/registry/data-source.schema.json`）下的数据源描述由 `sources/*.yaml` 自动导出到 `catalog/`，能通过团队的校验脚本；两边不会只改一边。

# 背景
开发线：D 目录线。团队规范的统一描述标准（`docs/interoperability.md` §1，PLAN 第 1.1 节：采纳但不强制）。`tests/validate_schemas.py` 会按目录前缀 `catalog` 用 data-source schema 校验其中的 JSON。来源注册表在 `sources/*.yaml`，加载代码在 `core/registry.py`。说明字段不作硬性要求：不知道的填 `"unknown"`，不编造。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-403/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `core/registry_export.py`：`export_registry(sources_dir, out_dir) -> list[Path]`，每个来源一个 `catalog/<source_id>.json`，键排序、缩进 2、UTF-8、末尾换行，内容只由 yaml 决定（不含生成时间），重复运行结果相同。
2. 字段映射：`id`；`name`；`country_region` 为大写国家码；`publisher` 取 `license.attribution` 里的机关名（取不到填 `unknown`）；`official_url` 为 `base_url`；`access_method` 按 `kind` 映射（`ckan`、`egov_law`、`stat_dashboard`、`jma_feed` → `api`，`estat_files` → `download`，`gov_web` → `web`，未知 → `unknown` 或 schema 允许的最接近值，写清规则）；`rate_limit` 为 `<rate_per_sec> req/s`；`auth_required` 由 `needs_key` 判断；`license` 的 `name`、`url`、`allows_redistribution`（`allowed` → true，`forbidden` → false，`unverified` → `"unknown"`）、`requires_attribution`；`last_verified` 为 `license.verified_at` 或 `unknown`；`status` 为 `experimental`。只输出 schema 里有的字段，值必须符合 schema。
3. CLI：`datumoj registry export [--out catalog]`。
4. 提交生成的 `catalog/*.json`。测试：导出结果通过 `schemas/registry/data-source.schema.json` 校验（用 jsonschema）；重新导出与已提交的 `catalog/` 完全一致（防止只改 yaml 不导出）；映射规则逐条有测试。
5. 在 `docs/interoperability.md` 末尾加一节“与 DATUMOJ 内部模型的映射”：`sources/*.yaml` 字段 ↔ 注册层字段；`Citation` 字段 ↔ §3.1 证据链字段（来源机构 ↔ 来源注册表、官方 URL ↔ `resource_url`、原始记录 ID ↔ `snapshot_sha256` + `locator`、采集时间 ↔ `fetched_at`、发布时间 ↔ `published_at` / `period` / `time_basis`、许可 ↔ `license` / `usage_scope`）。

# 不做
- 不把说明字段改成必填，不改 `schemas/registry/`。
- 不改 `sources/*.yaml`。
