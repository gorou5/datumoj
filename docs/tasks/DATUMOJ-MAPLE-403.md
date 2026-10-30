---
id: DATUMOJ-MAPLE-403
title: data.europa.eu 适配器：走 repo 接口（不碰 robots 禁止的 search），Publications Office 目录
project: DATUMOJ
status: ready
priority: 2
depends_on: []
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/data_europa.py", "src/datumoj/rikolto/parsers/data_europa.py", "sources/eu-data-europa.yaml", "tests/crawlers/test_data_europa.py", "tests/parsers/test_data_europa.py", "tests/integration/test_data_europa_e2e.py", "catalog/eu-data-europa.json", "results/DATUMOJ-MAPLE-403/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/wiring.py", "src/datumoj/eltiro/**", "sources/README.md"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_data_europa.py
    - cmd: make check T=tests/parsers/test_data_europa.py
    - cmd: make check T=tests/integration/test_data_europa_e2e.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-403/summary.md 完成说明"]
  review: {by: other_family, checklist: ["许可、署名、allowed_hosts、频率和上限是否和来源决策文档一致", "是否只请求白名单主机、遵守 robots，不绕过", "单位、时间、注记是否原样保留，没有补零或自行换算", "是否有联网的测试", "是否改了契约、wiring 或其他来源的实现", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
data.europa.eu 上欧盟出版局（catalogue `publ`）的数据集能在目录检索里搜到，多语言标题都保留。

# 背景
来源决策文档 3.4（data.europa.eu）、4.6、6.3–6.4 节；样本 `tests/fixtures/data_europa/`（`publ` 的 ID 列表一页、一条详情 JSON-LD）。
- **只用 repo 接口**：先 `/api/hub/repo/catalogues/publ/datasets?valueType=identifiers&limit=&offset=` 列 ID，再逐个取 `/api/hub/repo/datasets/<id>`。search 接口命中 robots 的 `Disallow: /*search?*`，带 `sort` 的 URL 命中 `Disallow: /*&sort=*`。
- 目录来源的写法参照已有的 `ckan` 和 `ckan_meta`。

计划见 `docs/plans/0.3-maple.md`（第 5、6.6 节）和来源决策 `docs/plans/0.3-maple-sources.md`（下文列出的节）。本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **按插件接入**：`crawlers/` 加一个模块（`Crawler` 子类声明 `kind`），`parsers/` 加一个模块（`Parser` 子类声明 `source_kind`），再加 `sources/<id>.yaml`；`discover_crawlers` / `discover_parsers` 会自动发现，不改 `wiring.py`。写法参照已有的 `stat_dashboard`（API → 观测值）、`ckan`（目录 → Dataset）、`estat_files`（文件 → 观测值）。
- 契约（`core/contracts.py`、`core/models.py`）和 schema 不改；需要改时写进 `summary.md` 并停下来问。
- 样本在 `tests/fixtures/<目录>/`（H1，PR #32 已合并），只读；每个目录的 README 写了 URL、过滤参数、样本类型和许可。测试全部离线，HTTP 只用这些样本或 `pytest-httpserver`。
- yaml 的值按决策文档：`schedule` 见 6.1，`rate_per_sec` / `max_total_bytes` / `max_files` 见 6.2，`allowed_hosts` 见 6.3，许可的 `name` / `url` / `attribution` 见第 4 节；核查完之前一律 `usage_scope: internal_only`、`redistribution: unverified`、`verified_at: null`。来源开关在调度状态里，默认关闭，本任务不打开。
- 不新增依赖，不改 `pyproject.toml`、`uv.lock`；仓库是公开的，不写账号、密钥、本机绝对路径；临时文件放 `tmp_path`。

# 要求
1. **yaml** `sources/eu-data-europa.yaml`：`country: eu`，`allowed_hosts: [data.europa.eu]`，`schedule: daily`，`scope.catalogue: publ`，`download_resources: false`；门户元数据许可 CC0 1.0，资源本体许可另记。
2. **采集**（`kind: data_europa_repo`）：按 `limit` + `offset` 翻 ID 列表直到返回不足一页；再逐个取详情。URL 里不出现 `search` 和 `sort` 参数（加断言）。逐个取详情的请求数受 `max_files` 约束，超了就停并记原因。
3. **解析**（`source_kind: data_europa_repo`）：
   - 详情是 DCAT JSON-LD：每个数据集一个 `Dataset`（键用 repo 的数据集 ID），标题和描述按语言全部保留进 `extras`，检索内容至少包含英语、法语、德语（有就放），主标题优先英语；每个 distribution 一个 `Resource`（格式、URL、许可），不下载。
   - 不假设许可、主题、发布者是短字符串：可能是受控词表 URI，原样记录。
4. **测试**：
   - 解析：样本详情的多语言标题、distribution 数、许可 URI 原样。
   - 采集：翻页到不足一页时停；请求的 URL 都不命中决策文档里那两条 robots 规则（用已有的 robots 判定类断言）。
   - `test_data_europa_e2e.py`：样本离线入库，用英文关键词（如 “combined nomenclature”）在目录检索里找到它。
5. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不用 search 接口、SPARQL。
- 不采 `estat` catalogue（和 Eurostat 来源重复）。

# 补充：来源目录文件（2026-10-31）
- 新来源要同时提交 `catalog/eu-data-europa.json`（仓库里登记来源的导出目录，`tests/core/test_registry_export.py` 和 `tests/integration/test_tachibana_m2.py` 要求它和 `sources/*.yaml` 一致）。用 `uv run --frozen rikolto registry export --out catalog` 生成，只提交本任务来源的文件；`catalog/` 下其他文件不应有变化，有变化就说明改到了别的来源，要查清楚。
- `tests/regressions/test_audit_wisteria_102.py` 已在 `feature/0.4` 上改为只固定原有的 7 个来源（提交 42efc70），本任务不需要、也不许改它。
