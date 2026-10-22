# DATUMOJ 0.1（代号 IVY）任务拆分

> 计划第 3 版（2026-10-20），配合 `docs/PLAN.md` 第 7、8 节使用。任务编号 `DATUMOJ-IVY-<序号>`，下文表格里为简洁只写序号。
> 任务文件的审核副本在 `docs/tasks/DATUMOJ-IVY-*.md`（进 git，供团队审核）。审核通过后，再复制到调度器读取的 `.orch/tasks/`（不进 git）。
> 本文件是给人看的总表，**以任务文件为准**；两边不一致时先改任务文件，再同步这里。说明见 `docs/tasks/README.md`。

## 1. 两类工作

| 类型 | 谁来做 | 例子 |
|---|---|---|
| **开发任务 DATUMOJ-IVY-xxx** | 调度器派给 agent，离线测试验收，审查后合并 | 写契约、采集器、解析器、索引、MCP、评测题 |
| **主对话步骤 H-x** | 人（各线负责人）在主对话里和 Claude 一起做 | 搭骨架、锁依赖、录制真实样本、真实抓取、验收、打 tag |

agent 只能跑 pytest，不能联网、不能装依赖。所以凡是要**联网、装东西、改依赖、做判断**的事，都放在 H 步骤里。

## 2. 并行结构

按依赖分层后共 8 层（串行版的关键路径约 12 步）。同一层的任务互不依赖，可以同时跑，调度器同时最多跑 3 个：

| 层 | 可同时开工的任务 | 说明 |
|---|---|---|
| 1 | 001 契约一、003 日文文本 | 起点 |
| 2 | 002 契约二、301 表格、302 地区码 | 契约二做完，所有线解锁 |
| 3 | 101–104（A 线 4 个）、202 法令解析、303 观测值库、402 目录库、501 FTS、502 向量、601 流水线、603 工具层、701 评测框架 | **最宽的一层，共 12 个**，7 条线全部在跑 |
| 4 | 105 执行器、201 法令采集、203 JMA、304 Dashboard、305 e-Stat、401 CKAN、503 混合检索、604 MCP、702–704 题库 | 共 11 个 |
| 5 | 602 CLI | |
| 6 | 901 检查点 M1 集成 | 打 tag `v0.1.0-ivy-m1` |
| 7 | 902 检查点 M2 集成 | 打 tag `v0.1.0-ivy-m2` |
| 8 | 903 检查点 M3 集成 | 打 tag `v0.1.0-ivy-m3`；H6 验收后打 `v0.1.0-ivy`，IVY 完成 |

```
         ┌─ A: 101 102 103 104 ─► 105 ─────────────────┐
         ├─ B: 202 ; 201 203 ──────────────────────────┤
001 ─► 002 ┼─ C: 303 ; 304 305（另需 301 302）─────────────┤
003 ─────┤ ├─ D: 402 ; 401 ───────────────────────────────┼─► 901 ─► 902 ─► 903
         ├─ E: 501 502 ─► 503 ─────────────────────────┤    (M1)     (M2)    (M3)
         ├─ F: 601 ─► 602 ; 603 ─► 604 ────────────────┤
         └─ G: 701 ─► 702 703 704 ─────────────────────┘
```

**并行不冲突的保证**：
- 不存在依赖关系、可能同时运行的任务，scope 两两不重叠（已用脚本检查过）。
- 契约文件（`core/models.py`、`core/contracts.py`、`testing/**`）对所有开发线任务都是 protected。
- 采集器和解析器靠插件自动发现，不需要共享登记文件。

## 3. 主对话步骤（H）

### H0 骨架与依赖（项目负责人，派任务之前）
1. `pyproject.toml`：Python `>=3.12,<3.14`，src 布局，入口 `datumoj = "datumoj.cli:app"`。
2. 依赖一次锁定：
   - 运行：`httpx`、`pydantic>=2`、`pyyaml`、`typer`、`lxml`、`openpyxl`、`pypdfium2`、`pdfplumber`、`charset-normalizer`、`duckdb`、`pyarrow`、`lancedb`、`mcp`
   - 开发组：`pytest`、`pytest-httpserver`、`ruff`
3. pytest：`--import-mode=importlib`，`testpaths = ["tests"]`。
4. 空包：`src/datumoj/{core,crawlers,parsers,store,index,analysis,eval,reference,text,tabular,testing}/__init__.py`；`tests/` 各子目录；`schemas/`、`sources/`、`eval/questions/`、`skills/datumoj/`。
5. `Makefile`：`check T=`（任务验收用，等价于 `uv run --frozen pytest -q $(T)`）、`test`、`lint`、`crawl SOURCE= LIMIT=`、`parse`、`index`、`eval`、`mcp`。`UV` 变量先在 PATH 里找 `uv`，找不到再退回 `/opt/homebrew/bin/uv`，可以用环境变量覆盖。
6. `.gitignore` 追加 `data/`、`.secrets/`、`.venv/`、`handoff/**/*.zip`、`handoff/**/*.xlsx`。
7. 执行 `uv lock` 和 `uv run --frozen pytest -q`，确认依赖缓存已经就位，agent 的 checkout 才能离线安装。
8. 提交并推送（PR）。

### H1 录制测试样本（各线负责人并行，派第 3 层任务之前完成）

每个目录都写 README.md，说明每个文件的来源 URL（去掉 Key）、抓取时间、用途、许可。真实请求一律有限次数，1 请求/秒。

**注意：本仓库是公开仓库**，fixture 一旦提交就等于再分发。只提交许可允许再分发的样本（附署名），个人信息必须先脱敏；不确定的先问项目负责人。外部团队提供的资料先放在 `docs/references/`（规范见该目录 README），再从中挑选。

| 负责人 | 目录 | 内容 |
|---|---|---|
| 甲（B 线） | `tests/fixtures/egov_law/` | 法令列表 2 页，约 25 部法令的现行版 XML 和修订信息。选个人信息保护法、行政手続法、地方自治法、补助金适正化法、統計法、公文書管理法，以及带“条の二”、附则、别表的法令；再做一个损坏的 base64 样本和一个含实体声明的恶意 XML |
| 甲（B 线） | `tests/fixtures/jma/` | 长期 feed 两次（新旧条目重叠），3–5 个条目 XML（含旧项目的大阪预报） |
| 乙（C 线） | `tests/fixtures/stat_dashboard/` | 沿用旧项目的两个样本，再补一次分批 `getData` 响应 |
| 乙（C 线） | `tests/fixtures/estat_files/` | 两种版式的真实统计表，各 1–2 个（cp932 CSV 和 XLSX 各至少一个） |
| 乙（C 线） | `tests/fixtures/tabular/` | 旧项目踩过坑的样本：cp932 但响应头写 UTF-8、ZIP 混合编码、伪装成 XLSX 的 ZIP |
| 乙（C 线） | `tests/fixtures/area_codes/`、`src/datumoj/reference/data/` | 总务省“全国地方公共団体コード”最新版 |
| 乙（D 线） | `tests/fixtures/ckan_egov/` | `package_search` 两页（每页缩成 5 条）、一次增量接口响应、两个资源文件 |

### H2 契约审查与 M0 检查点（项目负责人 + 全体）
- DATUMOJ-IVY-001 和 DATUMOJ-IVY-002 进入 review 后，全体负责人一起审契约。契约决定了自己那条线怎么写，所以每个人都要看。
- A 线完成（DATUMOJ-IVY-105 合并）后，用回放样本走一遍“计划→抓取→存 raw→写 manifest”，然后打 tag `v0.1.0-ivy-m0`。

### H3 M1 验收（甲 + 丁）
- `make crawl SOURCE=jp-egov-law LIMIT=200`，接着 `parse`、`index`；用 5–10 个真实问题执行 `datumoj search`；重复 crawl，确认没有重复下载。
- 记录到 `docs/acceptance/v0.1.0-ivy-m1.md`，打 tag。然后决定是否全量抓取法令（约 9000 部，按 1 请求/秒约 3 小时）。

### H4 M2 验收（乙）
- 真实抓取 e-Gov 目录（先限 1000 个数据集）、Dashboard 精选指标、e-Stat 精选表；`datumoj sql` 查都道府县人口，核对 3 个数值和原表一致。
- **开始 JMA 定时轮询**：每小时执行 `datumoj crawl jp-jma`，日志写到 `data/logs/`。在项目负责人的 Mac 上用 launchd；`make schedule` 同时能生成 cron 配置，方便以后换到 Linux。
- 记录到 `docs/acceptance/v0.1.0-ivy-m2.md`，打 tag。

### H5 嵌入模型选型（丙，M1 之后随时可做）
- 经同意后在本机安装 Ollama，拉取 `bge-m3`（约 1.2GB）。
- 在法令语料上比较“纯关键词”和“关键词 + bge-m3”的 Recall@10，结论写进 `docs/adr/0001-embedding.md`；估算全量嵌入耗时。
- 同时在全量法令（约 100 万块）上测两个字以下短查询的耗时（这类查询退回 LIKE 全表扫描），据此调整结果上限和超时。

### H6 M3 与 IVY 版本验收（全体）
- 把 `datumoj mcp` 注册进 Claude Code，第一步先调用 `version` 核对加载的版本。
- 现场问 10 个问题：交接包 06 号文档里适用的验收问题改写版，加上 PLAN 第 8 节的示例问题。逐条记录问题、工具调用、回答、出处是否属实。
- **人工抽查引用支持率**：从回答里抽 20 条引用，检查原文是否真的支持对应结论，目标 ≥ 0.95，结果写进验收文档。
- 记录到 `docs/acceptance/v0.1.0-ivy.md`，打 tag `v0.1.0-ivy-m3` 和 `v0.1.0-ivy`。IVY 版本完成，第一阶段完成。

## 4. 开发任务总表（共 32 个）

验收命令统一是 `make check T=<下表的测试路径>`，全部离线。所有任务的 `protected` 都包含 `tests/fixtures/**`；契约以外的任务还把契约文件列为 protected。

| ID | 标题 | 依赖 | runner / 难度 | 审查 | 验收 |
|---|---|---|---|---|---|
| **契约** ||||||
| DATUMOJ-IVY-001 | 契约一：数据模型、locator、工具输出格式 | — | claude / high | human | `tests/core/test_models.py` |
| DATUMOJ-IVY-002 | 契约二：接口协议、插件自动发现、假实现与契约测试 | 001 | claude / high | human | `tests/core/test_contracts.py` |
| DATUMOJ-IVY-003 | 日文文本工具：解码、归一化、和历、数字注记 | — | codex / medium | other_family | `tests/text/` |
| **A 抓取基础**（负责人：甲） ||||||
| DATUMOJ-IVY-101 | raw 存储：按内容寻址 | 002 | codex / medium | other_family | `tests/core/test_rawstore.py` |
| DATUMOJ-IVY-102 | manifest：抓取记录库（SQLite） | 002 | claude / medium | other_family | `tests/core/test_manifest.py` |
| DATUMOJ-IVY-103 | 礼貌 HTTP 客户端：限速、条件请求、重试、上限、回放 | 002 | claude / high | other_family | `tests/core/test_http.py` |
| DATUMOJ-IVY-104 | 来源注册表 schema 与校验 | 002 | codex / medium | other_family | `tests/core/test_registry.py` |
| DATUMOJ-IVY-105 | 采集执行器：计划→抓取→落库→记录，带上限 | 101, 102, 103, 104 | claude / high | other_family | `tests/core/test_runner.py` |
| **B 文档线**（负责人：甲） ||||||
| DATUMOJ-IVY-201 | e-Gov 法令采集器 | 002, 104 | claude / medium | other_family | `tests/crawlers/test_egov_law.py` |
| DATUMOJ-IVY-202 | 法令 XML 解析器：按条切分，带层级和 locator | 002, 003 | codex / medium | other_family | `tests/parsers/test_xml_law.py` |
| DATUMOJ-IVY-203 | JMA 长期 feed 采集器与解析器 | 002, 003, 104 | codex / medium | other_family | `tests/crawlers/test_jma_feed.py` |
| **C 统计线**（负责人：乙） ||||||
| DATUMOJ-IVY-301 | 表格读取：CSV / XLSX / ZIP，cp932，表头与注记 | 003 | codex / high | other_family | `tests/tabular/` |
| DATUMOJ-IVY-302 | 地区码表：全国地方公共団体コード | 003 | codex / medium | other_family | `tests/reference/` |
| DATUMOJ-IVY-303 | 观测值库：DuckDB + Parquet，只读 SQL 与出处回溯 | 002 | claude / high | human | `tests/store/test_observations.py` |
| DATUMOJ-IVY-304 | 统计 Dashboard 采集器、分批规划与观测值转换 | 104, 302 | claude / medium | other_family | `tests/crawlers/test_stat_dashboard.py` |
| DATUMOJ-IVY-305 | e-Stat 统计表文件采集器与两类版式转换 | 104, 301, 302 | claude / high | other_family | `tests/crawlers/test_estat_files.py` |
| **D 目录线**（负责人：乙） ||||||
| DATUMOJ-IVY-401 | 通用 CKAN 采集器（e-Gov データポータル） | 002, 104 | claude / medium | other_family | `tests/crawlers/test_ckan.py` |
| DATUMOJ-IVY-402 | 目录解析、目录存储与目录检索 | 002 | codex / medium | other_family | `tests/store/test_catalog.py` |
| **E 检索线**（负责人：丙） ||||||
| DATUMOJ-IVY-501 | chunk 存储与 FTS5 trigram 关键词索引 | 002, 003 | claude / medium | other_family | `tests/index/test_fts.py` |
| DATUMOJ-IVY-502 | 向量索引：嵌入接口与 LanceDB | 002 | claude / medium | other_family | `tests/index/test_vector.py` |
| DATUMOJ-IVY-503 | 混合检索（RRF、多查询）与四种缺口状态 | 501, 502, 102, 104 | codex / high | other_family | `tests/index/test_hybrid.py` |
| **F 入口线**（负责人：丙） ||||||
| DATUMOJ-IVY-601 | 解析与索引流水线（增量，基于插件发现） | 002 | claude / medium | other_family | `tests/test_pipeline.py` |
| DATUMOJ-IVY-602 | 命令行：crawl / parse / index / search / sql / status | 601, 105 | claude / medium | other_family | `tests/test_cli.py` |
| DATUMOJ-IVY-603 | 分析工具层：search / catalog / sql / chunk / cite / version / coverage | 002 | claude / high | human | `tests/analysis/test_tools.py` |
| DATUMOJ-IVY-604 | MCP 服务器（stdio） | 603 | claude / medium | other_family | `tests/test_mcp_server.py` |
| **G 评测线**（负责人：丁） ||||||
| DATUMOJ-IVY-701 | 评测框架：题库格式、全部指标、门槛 | 002 | codex / medium | other_family | `tests/eval/test_framework.py` |
| DATUMOJ-IVY-702 | 法令评测题 20 道 | 701 | claude / medium | human | `tests/eval/test_questions_law.py` |
| DATUMOJ-IVY-703 | 目录与统计评测题 20 道 | 701 | claude / medium | human | `tests/eval/test_questions_stats.py` |
| DATUMOJ-IVY-704 | 缺口、时间、跨语言、注入评测题 + 回答规则 skill | 701 | claude / medium | human | `tests/eval/test_questions_behavior.py` |
| **集成**（负责人：项目负责人） ||||||
| DATUMOJ-IVY-901 | 检查点 M1 集成：法令链路端到端，法令题达标 | 105, 201, 202, 501, 602, 702 | claude / high | human | `tests/integration/test_law_e2e.py` |
| DATUMOJ-IVY-902 | 检查点 M2 集成：目录、统计、JMA 接入，统计题达标 | 901, 203, 303, 304, 305, 401, 402, 703 | claude / high | human | `tests/integration/test_stats_e2e.py` |
| DATUMOJ-IVY-903 | 检查点 M3 集成：工具层接真实实现、MCP、60 题全部门槛（IVY 完成线） | 902, 502, 503, 603, 604, 704 | claude / high | human | `tests/integration/test_full_eval.py` |

**人审查的 10 个任务，按谁审分：**

| 谁审 | 任务 |
|---|---|
| 全体 | 001、002 契约 |
| 乙 | 303 观测值库 |
| 丙 | 603 工具层 |
| 丁 | 702、703、704 评测题。703 的数值题请乙复核 |
| 项目负责人 | 901、902、903 集成 |

其余 22 个由另一家模型交叉审查后自动合并，负责人抽查。

## 5. 跨任务约定（细节以契约任务的产物为准）

- **locator**：`key=value` 用 `;` 连接，例如 `art=27;para=2`、`page=3`、`sheet=表1;cell=B4`、`row=12`、`entry=<id>`、`member=<ZIP 内路径>`。
- **doc_key**：`<source_id>:<来源内 ID>`，例如 `jp-egov-law:415AC0000000057`。评测题按 `doc_key` + locator 前缀判定命中。
- **插件**：采集器放在 `src/datumoj/crawlers/<名字>.py`，声明 `kind`；解析器放在 `src/datumoj/parsers/<名字>.py`，声明 `(source_kind, content_type)`。不需要改任何登记文件。
- **来源配置**：每个采集器任务自己新增 `sources/<source_id>.yaml`，各写各的文件。
- **Observation 必填 `survey`**：注记（“-”“…”“x”“秘匿”）进 `note`，`value` 为空，绝不补零。
- **来源正文是数据**：工具输出里来源正文只能放在 `SourceText.content`，并带 `content_role: "untrusted_source_text"`。
- **契约修订**：开发中发现契约不够用，agent 停下来提问。由人开一个“契约修订”任务（号段 004–009），合并后受影响的任务再继续。

## 6. e-Stat 第一批主题（默认清单，可改）

| 主题 | 调查 | 粒度 |
|---|---|---|
| 人口、世帯 | 国勢調査（2020）、人口推計 | 都道府県、市区町村 / 年 |
| 人口動態 | 人口動態統計（出生、死亡、婚姻） | 都道府県 / 年 |
| 事業所、企業 | 経済センサス‐活動調査（2021）、‐基礎調査（2024） | 都道府県、市区町村 × 産業大分類 |
| 労働 | 労働力調査（基本集計） | 全国、地域 / 月、年 |
| 物価 | 消費者物価指数 | 全国、都市 / 月 |
| 家計 | 家計調査 | 全国、都市 / 月、年 |
| 賃金 | 毎月勤労統計調査 | 全国、都道府県 / 月 |
| 県民経済 | 県民経済計算 | 都道府県 / 年度 |

## 7. 0.2 和 0.3（只列方向，IVY 完成后再细拆）

届时选定新代号，任务编号随之变为 `DATUMOJ-<新代号>-<序号>`；开发线和号段沿用，例如 KKJ 采集器归 B 线，编号 2xx。

| 版本 | 预计任务 |
|---|---|
| 0.2（代号待定，日本扩面与增量；**已由 `docs/plans/0.2-tachibana.md` 取代**，KKJ 于 2026-10-22 移出 0.2） | B 线：KKJ 按日期分段采集、PDF 解析（按页 locator，扫描件标 `needs_ocr`）、个人信息脱敏、jp-gov-web 定向采集与 HTML 正文抽取。D 线：BODIK（复用 CKAN，加 DataStore）。A 线：增量调度（launchd 生成器）。F 线：变化检测与每周变化报告。G 线：M4 来源评测题 |
| 0.3（代号待定，欧美） | D 线：CKAN 多语言适配（data.europa.eu、英德法荷）。C 线：Eurostat SDMX、World Bank WDI。B 线：Federal Register 采集器。G 线：多语言检索评测 |
