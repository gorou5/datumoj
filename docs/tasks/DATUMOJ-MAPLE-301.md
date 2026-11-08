---
id: DATUMOJ-MAPLE-301
title: e-Stat API 3.0 精选表 → 观测值：appId 从环境变量取、只在内存里，一张表一个快照
project: DATUMOJ
status: done
priority: 2
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/estat_api.py", "src/datumoj/rikolto/parsers/estat_api.py", "sources/jp-estat-api.yaml", "tests/crawlers/test_estat_api.py", "tests/parsers/test_estat_api.py", "tests/integration/test_estat_api_e2e.py", "catalog/jp-estat-api.json", "results/DATUMOJ-MAPLE-301/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/wiring.py", "src/datumoj/eltiro/**", "sources/README.md", ".env*"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_estat_api.py
    - cmd: make check T=tests/parsers/test_estat_api.py
    - cmd: make check T=tests/integration/test_estat_api_e2e.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-301/summary.md 完成说明"]
  review: {by: other_family, checklist: ["appId 是否只从环境变量读、只出现在内存里的请求 URL；manifest、raw、快照 meta、日志、异常信息、summary 里都没有", "许可、署名、服务信用文本、allowed_hosts、频率和上限是否和来源决策文档一致", "是否只请求白名单主机、遵守 robots，不绕过", "单位、时间、注记、特殊符号是否原样保留，没有补零或自行换算", "多页响应是否被拒绝、不写半张表", "是否有联网的测试", "是否改了契约、wiring 或其他来源的实现", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
e-Stat 的人口推計、労働力調査、消費者物価指数三张精选表经 API 进 `query_sql`，每行可出处；appId 不落盘、不外泄。

# 背景
来源决策文档 3.1（e-Stat 一节）、4.1、6.1–6.4 节；样本 `tests/fixtures/estat_api/`（2026-11-01 录制，PR #77）。样本 README 里有用 getMetaInfo 确认的维度代码和时间代码格式，以它为准。

计划见 `docs/plans/0.3-maple.md`（第 5、6.6 节）和来源决策 `docs/plans/0.3-maple-sources.md`（下文列出的节）。本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **按插件接入**：`crawlers/` 加一个模块（`Crawler` 子类声明 `kind`），`parsers/` 加一个模块（`Parser` 子类声明 `source_kind`），再加 `sources/<id>.yaml`；`discover_crawlers` / `discover_parsers` 会自动发现，不改 `wiring.py`。写法参照已有的 `stat_dashboard`（API → 观测值）、`wdi`（一张表一个快照、多页拒绝）、`estat_tables`（e-Stat 文件 → 观测值）。
- 契约（`core/contracts.py`、`core/models.py`）和 schema 不改；需要改时写进 `summary.md` 并停下来问。
- 流水线每处理一个快照，就用它的观测值整体替换对应的 `table_key`，所以**一个快照必须含一张完整的表**。
- 样本只读；测试全部离线，HTTP 只用这些样本或 `pytest-httpserver`。
- yaml 的值按决策文档：`schedule` 见 6.1，`rate_per_sec` / `max_total_bytes` / `max_files` 见 6.2，`allowed_hosts` 见 6.3，许可见 4.1；核查完之前一律 `usage_scope: internal_only`、`redistribution: unverified`、`verified_at: null`。来源开关在调度状态里，默认关闭，本任务不打开。
- 不新增依赖，不改 `pyproject.toml`、`uv.lock`；仓库是公开的，不写账号、密钥、本机绝对路径；临时文件放 `tmp_path`。

# 要求
1. **yaml** `sources/jp-estat-api.yaml`：`country: jp`，`kind: estat_api`，`base_url: https://api.e-stat.go.jp/rest/3.0/app/json/`，`allowed_hosts: [api.e-stat.go.jp]`，`rate_per_sec: 1`，`max_total_bytes: 1073741824`，`max_files: 10000`，`schedule: daily`，**`needs_key: env:ESTAT_APP_ID`**。
   - `scope.tables` 列三张表，每张写 `stats_data_id`、过滤参数（照样本 README 的代码表）和 `unit`：
     - `0003443838`：`cdTab=001`、`cdCat01=001`、`cdCat02=000`、`cdCat03=01000`、`cdCat04=01`、`cdArea=00000`；单位 `万人`；取表里全部月份（目前 59 个月，体积很小，不用 `cdTimeFrom`）。
     - `0003005865`：`cdTab=02`、`cdCat01=000`、`cdCat02=01,13,08`、`cdCat03=0`、`cdArea=00000`；单位 `%`。
     - `0003427113`：`cdTab=1`、`cdCat01=0001`、`cdArea=00000`；单位 `2020年=100`。
   - `scope.stats_codes` 写目录范围 `00200524`、`00200531`、`00200573`（表 4 毎月勤労統計未定，不加）。
   - 许可：`name` 为「政府統計の総合窓口（e-Stat）利用規約」，`url: https://www.e-stat.go.jp/terms-of-use`，`attribution` 为「出典：政府統計の総合窓口(e-Stat)（https://www.e-stat.go.jp/）」；`notes` 写明 API 规约 `https://www.e-stat.go.jp/api/terms-of-use` 和决策文档 4.1 的服务信用文本原文。
2. **appId**（最重要）：
   - 采集器在 `plan` 里按 `ctx.spec.needs_key` 的变量名从环境变量取 appId；变量没设或为空时抛 `ValueError`，消息只写变量名。
   - appId 只作为 `appId` 查询参数放进 `FetchTask.url`（契约约定这里可以带 Key，落盘前由执行器 `redact_url`）；不放进 `FetchTask.meta`、不写日志、不进异常消息。
   - 不在代码里新增 `DATUMOJ_` 开头的环境变量名（`tests/regressions/test_audit_119.py` 固定了这份名单）。
3. **采集**（`kind: estat_api`）：
   - 每张表一次 `getStatsData` 请求：`lang=J`、yaml 里的过滤参数、`limit=100000`（API 单次上限，也是计划写的单次 10 万单元格上限）；`metaGetFlg` 用默认的 `Y`，响应里自带 `CLASS_INF`，不另外请求 `getMetaInfo`。
   - 每个 `stats_codes` 一次 `getStatsList`（`lang=J`、`statsCode`），按 `RESULT_INF.NEXT_KEY` 翻页取完目录，每页一个快照。
   - `plan` 校验 `stats_data_id` 是 10 位数字、过滤参数名只能是 `cdTab` / `cdCat01`–`cdCat15` / `cdArea` / `cdTime`，不合格抛 `ValueError`（yaml 加载器在 `core/` 里，不改）。
4. **解析**（`source_kind: estat_api`）：
   - `RESULT.STATUS` 为 0 正常；1（该当数据なし）不生成观测值并写 warning；其他值抛 `ParseError`，消息里带 `STATUS` 和 `ERROR_MSG`。
   - `getStatsData` 响应里有 `RESULT_INF.NEXT_KEY`，或 `TO_NUMBER < TOTAL_NUMBER`，就抛 `ParseError`：这个快照不发布任何观测值，不写半张表。
   - 每个 `VALUE` 一条 `Observation`：`table_key` 为 `statsDataId`；`dims` 写各分类的代码和名称（名称从 `CLASS_INF` 查）；`area_code` 用 `@area`；`unit` 用 yaml 写的单位，`@unit` 存在且与 yaml 不一致时抛 `ParseError`。
   - 时间代码 10 位（年 4 位 + `00` 年/月 或 `10` 年度 + 4 位月份重复两遍或 `0000`）：月转成该月首末日、`granularity` 为月。表 3 的年、年度项不生成观测值，在 warnings 里计数；表 1、2 出现非月度项就抛 `ParseError`。
   - 值是数字就原样转 `Decimal`；是 `DATA_INF.NOTE` 里列出的特殊符号（如 `***`、`-`）时 `value` 为 null、`note` 写符号和说明原文；其他文字抛 `ParseError`。
   - 每张表一个 `Dataset`：调查名（`STAT_NAME`）、政府統計コード、表标题、提供机关（`GOV_ORG`）、`OPEN_DATE` / `UPDATED_DATE`、`dbview?sid=` 表页面、许可、出处和服务信用文本。`getStatsList` 的每条目录也生成 `Dataset`。
   - `release_id` / `released_at` 留空（ADR 0007：e-Stat 没有发布版本号，`UPDATED_DATE` 只记在 Dataset 里）。
5. **测试**：
   - 解析：样本 `getStatsData_..._start1_limit10.json` 含 `NEXT_KEY`，用它测"多页响应被拒绝、不发布"；正常解析用它的数据在 `tmp_path` 里构造单页响应（去掉 `NEXT_KEY`，`TOTAL_NUMBER` 等于行数），不改样本。断言 2021 年 12 月総人口 12547 万人、维度名称来自 `CLASS_INF`、特殊符号变成 null 加注记、`STATUS` 非 0 时的两种行为。
   - 表 3 的时间过滤：用 `getMetaInfo_0003427113.json` 里的时间代码在 `tmp_path` 构造几行值，断言只留月度项、年和年度计入 warnings。
   - 采集：环境变量缺失时 `plan` 抛 `ValueError` 且消息里没有任何 Key 值；请求参数（`limit=100000`、过滤参数）正确；`getStatsList` 按 `NEXT_KEY` 翻页；非法 `stats_data_id` 或参数名被拒。
   - **泄漏检查**：`tests/integration/test_estat_api_e2e.py` 用 `monkeypatch.setenv` 设一个假 appId，样本离线走完采集、解析、入库，然后读遍 `tmp_path` 下所有文件的字节和捕获的日志，断言都不含这个假 appId；`query_sql` 查表 1 的值能给出出处，出处 URL 里是 `appId=REDACTED`。
6. 生成 `catalog/jp-estat-api.json`：用 `uv run --frozen rikolto registry export --out catalog`，只提交本来源的文件；`catalog/` 下其他文件不应有变化。
7. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不加表 4（毎月勤労統計），statsDataId 还没定。
- 不改 `.github/workflows/`（把 `ESTAT_APP_ID` secret 映射进 Actions 由主对话另提 PR）。
- 不打开来源开关，不做真实联网抓取（真实抓取在检查点 902）。
- 不判断表 2 是原数值还是季调值，原样记下表标题和说明。
