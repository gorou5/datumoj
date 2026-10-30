---
id: DATUMOJ-MAPLE-302
title: Eurostat SDMX 精选数据集 → 观测值：三套过滤条件、SDMX-ML 2.1、按快照保留每次抓到的值
project: DATUMOJ
status: ready
priority: 2
depends_on: []
runner: codex
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/eurostat.py", "src/datumoj/rikolto/parsers/eurostat.py", "sources/eu-eurostat.yaml", "tests/crawlers/test_eurostat.py", "tests/parsers/test_eurostat.py", "tests/integration/test_eurostat_e2e.py", "results/DATUMOJ-MAPLE-302/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/wiring.py", "src/datumoj/eltiro/**", "sources/README.md"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_eurostat.py
    - cmd: make check T=tests/parsers/test_eurostat.py
    - cmd: make check T=tests/integration/test_eurostat_e2e.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-302/summary.md 完成说明（含观测状态到 note 的对照表、obs_id 规则）"]
  review: {by: other_family, checklist: ["许可、署名、allowed_hosts、频率和上限是否和来源决策文档一致", "是否只请求白名单主机、遵守 robots，不绕过", "单位、时间、注记是否原样保留，没有补零或自行换算", "是否有联网的测试", "是否改了契约、wiring 或其他来源的实现", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
Eurostat 的 GDP、人口和希腊政府赤字 / 债务数据进 `query_sql`，每行可出处；尽早每天抓，开始积累 Eurostat 的修订历史（0.4 希腊赤字案例要用）。

# 背景
来源决策文档 3.1（Eurostat 一节）、4.2、6.1–6.4 节；样本 `tests/fixtures/eurostat/`（`gov_10dd_edpt1` 和 `nama_10_gdp` 的数据与 DSD）。ADR 0007：来源发布编号只能来自来源的发布记录，不能伪造。

计划见 `docs/plans/0.3-maple.md`（第 5、6.6 节）和来源决策 `docs/plans/0.3-maple-sources.md`（下文列出的节）。本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **按插件接入**：`crawlers/` 加一个模块（`Crawler` 子类声明 `kind`），`parsers/` 加一个模块（`Parser` 子类声明 `source_kind`），再加 `sources/<id>.yaml`；`discover_crawlers` / `discover_parsers` 会自动发现，不改 `wiring.py`。写法参照已有的 `stat_dashboard`（API → 观测值）、`ckan`（目录 → Dataset）、`estat_files`（文件 → 观测值）。
- 契约（`core/contracts.py`、`core/models.py`）和 schema 不改；需要改时写进 `summary.md` 并停下来问。
- 样本在 `tests/fixtures/<目录>/`（H1，PR #32 已合并），只读；每个目录的 README 写了 URL、过滤参数、样本类型和许可。测试全部离线，HTTP 只用这些样本或 `pytest-httpserver`。
- yaml 的值按决策文档：`schedule` 见 6.1，`rate_per_sec` / `max_total_bytes` / `max_files` 见 6.2，`allowed_hosts` 见 6.3，许可的 `name` / `url` / `attribution` 见第 4 节；核查完之前一律 `usage_scope: internal_only`、`redistribution: unverified`、`verified_at: null`。来源开关在调度状态里，默认关闭，本任务不打开。
- 不新增依赖，不改 `pyproject.toml`、`uv.lock`；仓库是公开的，不写账号、密钥、本机绝对路径；临时文件放 `tmp_path`。

# 要求
1. **yaml** `sources/eu-eurostat.yaml`：`country: eu`，`base_url` 为 SDMX 2.1 入口，`allowed_hosts: [ec.europa.eu]`，`schedule: daily`；`scope.datasets` 列出三个数据集，每个写 `id`、查询 key、`startPeriod`、`endPeriod`（值见决策文档 3.1：`nama_10_gdp` `A.CP_MEUR.B1GQ.DE+FR+NL` 2020–2024；`demo_pjan` `A.NR.TOTAL.T.DE+FR+NL` 2020–2024；`gov_10dd_edpt1` `A.PC_GDP+MIO_EUR.S13.B9+GD.EL` 2000–2015）。
2. **采集**（`kind: eurostat_sdmx`）：每个数据集取 DSD（`/datastructure/ESTAT/<ID>`，不用 `references=datastructure`）和数据（SDMX-ML 2.1 Generic，不用 `format=JSON`）。key 只允许维度代码字符和 `.`、`+`，在 yaml 加载时校验。
3. **解析**（`source_kind: eurostat_sdmx`）：
   - 每个观测值一条 `Observation`：`table_key` 为数据集 ID；`dims` 写全部维度代码，`indicator` / `unit` / `area_code` 取对应维度，`area_label`、单位名称从 DSD 的 codelist 取；`time_start` / `time_end` / `granularity` 按 `TIME_PERIOD`。
   - 观测状态（`OBS_STATUS`、`OBS_FLAG`，例如 `p` 暂定、`e` 估计、缺值）写进 `note`；缺值时 `value` 为空、`note` 写状态，绝不补零。
   - `obs_id` 由数据集、维度 key 和时间组成，同一次请求内稳定唯一。
   - `release_id` / `released_at` 留空：API 只给当前值，不能把抓取时间写成来源发布（ADR 0007；决策文档“历史年份的当前值 vs 历史发布版本”）。每次快照照常保留，旧值靠快照历史保存。
   - 每个数据集一个 `Dataset`，标题和说明取 DSD / dataflow 名称。
4. **测试**：
   - 解析：希腊 2009 年赤字率 `PC_GDP.S13.B9` 为 -15.4（决策文档实测值），单位 `PC_GDP`；`MIO_EUR` 与 `PC_GDP` 不混；有状态标记的观测值 `note` 带上标记。
   - 采集：请求的 URL、key 校验拒绝非法字符。
   - `tests/integration/test_eurostat_e2e.py`：用样本离线入库，`query_sql` 查到希腊 2009 年赤字率并能为该行生成出处；同一数据集换一份数值不同的合成快照再入库后，旧快照的值仍能按快照查到。
5. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不回补历史发布版本（EDP 通报档案属于 0.4 的缺口报告）。
- 不用 JSON-stat。
- 不跨币种、跨口径换算。
