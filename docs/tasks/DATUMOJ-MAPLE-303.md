---
id: DATUMOJ-MAPLE-303
title: 世界银行 WDI 精选指标 → 观测值：6 国、2000–2024、4 个指标，单位按指标写死
project: DATUMOJ
status: doing
priority: 2
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/wdi.py", "src/datumoj/rikolto/parsers/wdi.py", "sources/intl-wdi.yaml", "tests/crawlers/test_wdi.py", "tests/parsers/test_wdi.py", "tests/integration/test_wdi_e2e.py", "catalog/intl-wdi.json", "results/DATUMOJ-MAPLE-303/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/wiring.py", "src/datumoj/eltiro/**", "sources/README.md"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_wdi.py
    - cmd: make check T=tests/parsers/test_wdi.py
    - cmd: make check T=tests/integration/test_wdi_e2e.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-303/summary.md 完成说明"]
  review: {by: other_family, checklist: ["许可、署名、allowed_hosts、频率和上限是否和来源决策文档一致", "是否只请求白名单主机、遵守 robots，不绕过", "单位、时间、注记是否原样保留，没有补零或自行换算", "是否有联网的测试", "是否改了契约、wiring 或其他来源的实现", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
WDI 的人口、美元 GDP、本币 GDP、预期寿命作为跨国比较基准进 `query_sql`，每行可出处。

# 背景
来源决策文档 3.1（WDI 一节）、4.3、6.1–6.4 节；样本 `tests/fixtures/wdi/`（JPN、USA 2020–2024 的 GDP 美元 + 本币两页，和 `NY.GDP.MKTP.CN` 的指标元数据）。
- API 的 `unit` 字段是空的，单位只写在 sourceNote 里，所以单位必须按指标在 yaml 里写明（决策文档 3.1）。

计划见 `docs/plans/0.3-maple.md`（第 5、6.6 节）和来源决策 `docs/plans/0.3-maple-sources.md`（下文列出的节）。本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **按插件接入**：`crawlers/` 加一个模块（`Crawler` 子类声明 `kind`），`parsers/` 加一个模块（`Parser` 子类声明 `source_kind`），再加 `sources/<id>.yaml`；`discover_crawlers` / `discover_parsers` 会自动发现，不改 `wiring.py`。写法参照已有的 `stat_dashboard`（API → 观测值）、`ckan`（目录 → Dataset）、`estat_files`（文件 → 观测值）。
- 契约（`core/contracts.py`、`core/models.py`）和 schema 不改；需要改时写进 `summary.md` 并停下来问。
- 样本在 `tests/fixtures/<目录>/`（H1，PR #32 已合并），只读；每个目录的 README 写了 URL、过滤参数、样本类型和许可。测试全部离线，HTTP 只用这些样本或 `pytest-httpserver`。
- yaml 的值按决策文档：`schedule` 见 6.1，`rate_per_sec` / `max_total_bytes` / `max_files` 见 6.2，`allowed_hosts` 见 6.3，许可的 `name` / `url` / `attribution` 见第 4 节；核查完之前一律 `usage_scope: internal_only`、`redistribution: unverified`、`verified_at: null`。来源开关在调度状态里，默认关闭，本任务不打开。
- 不新增依赖，不改 `pyproject.toml`、`uv.lock`；仓库是公开的，不写账号、密钥、本机绝对路径；临时文件放 `tmp_path`。

# 要求
1. **yaml** `sources/intl-wdi.yaml`：`country: intl`，`base_url: https://api.worldbank.org/v2/`，`allowed_hosts: [api.worldbank.org]`，`schedule: monthly`；`scope` 写国家 `JPN USA GBR DEU FRA NLD`、年份 2000–2024，指标 `SP.POP.TOTL`、`NY.GDP.MKTP.CD`、`NY.GDP.MKTP.CN`、`SP.DYN.LE00.IN`，每个指标写单位（人、current US$、current LCU（各国本币）、年）。
2. **采集**（`kind: worldbank_wdi`）：请求带 `format=json`、`source=2`、`date=2000:2024`；按响应里的 `pages` 翻完所有页，每次重读整个区间（覆盖历史修订）；另取每个指标的元数据（名称、sourceNote、sourceOrganization）。只接受 yaml 里的国家代码，排除地区和收入组聚合。
3. **解析**（`source_kind: worldbank_wdi`）：
   - 每个国家 × 年份一条 `Observation`：`table_key` 为指标代码，`area_code` 用 ISO3，`unit` 用 yaml 写的单位，`time_start` / `time_end` 为整年。
   - `value` 为 null 的年份不生成观测值，在 `warnings` 里按指标计数（不补零，也不造注记）。
   - 本币 GDP 和美元 GDP 是不同的 `table_key`，不合并；本币 GDP 的 `dims` 记下“各国本币，未换算”。
   - 每个指标一个 `Dataset`：名称、sourceNote、数据提供方原文、许可（CC BY 4.0 + World Bank 数据集条款，决策文档 4.3）。
   - `release_id` / `released_at` 留空（ADR 0007）。
4. **测试**：
   - 解析：样本两页合起来的观测值条数、JPN 2023 年美元 GDP 的值与样本一致；本币 GDP 的 `unit` 不是美元；null 年份不出现、warnings 有计数。
   - 采集：按 `pages` 翻页、请求带 `source=2`；`scope` 里写聚合代码（如 `WLD`）时，采集器 `plan` 抛 `ValueError`（yaml 加载器在 `core/` 里，不改）。
   - `tests/integration/test_wdi_e2e.py`：样本离线入库，`query_sql` 比较 JPN 和 USA 的美元 GDP 能各自给出出处。
5. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不扩展到 WDI 全部指标或其他 World Bank 数据源。
- 不做币种换算。

# 补充：来源目录文件（2026-10-31）
- 新来源要同时提交 `catalog/intl-wdi.json`（仓库里登记来源的导出目录，`tests/core/test_registry_export.py` 和 `tests/integration/test_tachibana_m2.py` 要求它和 `sources/*.yaml` 一致）。用 `uv run --frozen rikolto registry export --out catalog` 生成，只提交本任务来源的文件；`catalog/` 下其他文件不应有变化，有变化就说明改到了别的来源，要查清楚。
- `tests/regressions/test_audit_wisteria_102.py` 已在 `feature/0.4` 上改为只固定原有的 7 个来源（提交 42efc70），本任务不需要、也不许改它。
