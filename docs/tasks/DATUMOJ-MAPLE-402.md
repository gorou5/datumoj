---
id: DATUMOJ-MAPLE-402
title: data.gouv.fr（udata）适配器：INSEE 的数据集目录，只采元数据不下资源
project: DATUMOJ
status: ready
priority: 2
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/udata.py", "src/datumoj/rikolto/parsers/udata.py", "sources/fr-data-gouv.yaml", "tests/crawlers/test_udata.py", "tests/parsers/test_udata.py", "tests/integration/test_udata_e2e.py", "results/DATUMOJ-MAPLE-402/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/wiring.py", "src/datumoj/eltiro/**", "sources/README.md"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_udata.py
    - cmd: make check T=tests/parsers/test_udata.py
    - cmd: make check T=tests/integration/test_udata_e2e.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-402/summary.md 完成说明"]
  review: {by: other_family, checklist: ["许可、署名、allowed_hosts、频率和上限是否和来源决策文档一致", "是否只请求白名单主机、遵守 robots，不绕过", "单位、时间、注记是否原样保留，没有补零或自行换算", "是否有联网的测试", "是否改了契约、wiring 或其他来源的实现", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
法国统计局（INSEE）在 data.gouv.fr 上的数据集能在目录检索里搜到，带许可和来源方的最后更新时间。

# 背景
来源决策文档 3.4（法国）、4.6、6.1–6.4 节；样本 `tests/fixtures/udata_fr/`（INSEE 列表两页 `page_size=2` 和一条详情）。
- udata 不是 CKAN：分页是 `page`（从 1 开始）+ `page_size`，响应带 `next_page`、`total`；`organization=` 只接受 24 位十六进制 id。
- 目录来源的写法参照已有的 `ckan` 采集器和 `ckan_meta` 解析器（产出 `Dataset`、`Resource` 和供目录检索的内容）。

计划见 `docs/plans/0.3-maple.md`（第 5、6.6 节）和来源决策 `docs/plans/0.3-maple-sources.md`（下文列出的节）。本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **按插件接入**：`crawlers/` 加一个模块（`Crawler` 子类声明 `kind`），`parsers/` 加一个模块（`Parser` 子类声明 `source_kind`），再加 `sources/<id>.yaml`；`discover_crawlers` / `discover_parsers` 会自动发现，不改 `wiring.py`。写法参照已有的 `stat_dashboard`（API → 观测值）、`ckan`（目录 → Dataset）、`estat_files`（文件 → 观测值）。
- 契约（`core/contracts.py`、`core/models.py`）和 schema 不改；需要改时写进 `summary.md` 并停下来问。
- 样本在 `tests/fixtures/<目录>/`（H1，PR #32 已合并），只读；每个目录的 README 写了 URL、过滤参数、样本类型和许可。测试全部离线，HTTP 只用这些样本或 `pytest-httpserver`。
- yaml 的值按决策文档：`schedule` 见 6.1，`rate_per_sec` / `max_total_bytes` / `max_files` 见 6.2，`allowed_hosts` 见 6.3，许可的 `name` / `url` / `attribution` 见第 4 节；核查完之前一律 `usage_scope: internal_only`、`redistribution: unverified`、`verified_at: null`。来源开关在调度状态里，默认关闭，本任务不打开。
- 不新增依赖，不改 `pyproject.toml`、`uv.lock`；仓库是公开的，不写账号、密钥、本机绝对路径；临时文件放 `tmp_path`。

# 要求
1. **yaml** `sources/fr-data-gouv.yaml`：`country: fr`，`base_url: https://www.data.gouv.fr/api/1/`，`allowed_hosts: [www.data.gouv.fr]`，`schedule: daily`；`scope.organization` 写 INSEE 的 id `534fff81a3a7292c64a77e5c`（yaml 加载时校验 24 位十六进制）；`download_resources: false`。许可 Licence Ouverte 2.0。
2. **采集**（`kind: udata`）：`/datasets/?organization=<id>&page_size=<n>&page=<k>` 翻到 `next_page` 为空；页数有上限（按 `total / page_size` 加余量），超过就停并记原因；不跟随资源链接下载文件。
3. **解析**（`source_kind: udata`）：
   - 每个数据集一个 `Dataset`（键用 udata 的永久 id，不用 slug）：标题、描述、机构、许可（原样记录 `license` 值）、标签、`modified_at` 用来源的 `last_modified`（不能用抓取时间代替，决策文档 4.6）、频率和时间覆盖进 `extras`。
   - 每个资源一个 `Resource`（标题、格式、mime、URL），不下载。
   - 目录检索的内容和 `ckan_meta` 一致：法语标题和描述能被关键词检索到。
4. **测试**：
   - 解析：样本两页共 4 个数据集，`modified_at` 等于样本的 `last_modified`；资源 URL 原样；联系人邮箱等个人信息不进检索块。
   - 采集：按 `next_page` 翻页、到最后一页停；`organization` 写 slug 时 yaml 加载报错。
   - `test_udata_e2e.py`：样本离线入库，用法语关键词（如 “population Iris”）在目录检索里找到对应数据集。
5. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不下载资源文件。
- 不采讨论、复用（reuses）、账号信息。
