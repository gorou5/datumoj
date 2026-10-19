# DATUMOJ 任务拆分（M0–M3）

> 版本 v0.1（2026-10-20），配合 `docs/PLAN.md` 使用。
> 任务文件的审核副本在 `docs/tasks/IVY-*.md`（进 git，供团队审核）。审核通过后，再复制到调度器读取的 `.orch/tasks/`（不进 git）。
> 本文件是给人看的总表，**以任务文件为准**；两边不一致时先改任务文件，再同步这里。说明见 `docs/tasks/README.md`。

## 1. 两类工作

| 类型 | 谁来做 | 例子 |
|---|---|---|
| **开发任务 IVY-xxx** | 调度器派给 agent，离线测试验收，异源审查后合并 | 写采集器、解析器、索引、MCP 服务器、评测题 |
| **主对话步骤 H-x** | 人在本会话里和 Claude 一起做 | 搭骨架、锁依赖、录制真实样本、真实抓取、里程碑验收、注册 MCP |

agent 只能跑 pytest，不能联网、不能装依赖，所以凡是要**联网、装东西、改依赖、做判断**的事都放在 H 步骤里。

## 2. 执行顺序总览

```
H0 骨架与依赖 ─► H1 法令样本 ─► IVY-001 ─┬─► 002 ─┐
                                          ├─► 003 ─┼─► 006 ─► 007 ─┐
                                          ├─► 004 ─┘                │
                 IVY-005 ─────────────────┼──────────► 008 ─────────┼─► 010 ─► 011 ─► 013 ─► H2 M1 验收
                                          ├─► 009 ──────────────────┘          ▲
                                          │     └─► 012 ───────────────────────┘
                                          └─► 018
H3 统计样本 ─► 014 ─► 015 ─► 019 ─┐
               016 ─────────► 020 ─┼─► 022 ─► H4 M2 验收 + 开始 JMA 定时轮询
               017 ───────────┘    │
               021（JMA） ─────────┘
H5 嵌入模型 ─► 023 ─► 024 ─► 025 ─► 026 ─► 027 ─► 028 ─► H6 M3 验收（在 Claude 里真实问答）
```

项目配额是同时 1 个任务（`max_concurrent: 1`），所以实际是串行执行；依赖关系决定了顺序，也保证以后调高并发时不会互相踩文件。

## 3. 主对话步骤（H）

### H0 骨架与依赖（派任何任务之前）
1. `pyproject.toml`：Python `>=3.12,<3.14`；包名 `datumoj`，src 布局；入口 `datumoj = "datumoj.cli:app"`。
2. 依赖（一次锁定，之后 agent 不能改）：
   - 运行：`httpx`、`pydantic>=2`、`pyyaml`、`typer`、`lxml`、`openpyxl`、`pypdfium2`、`pdfplumber`、`charset-normalizer`、`duckdb`、`pyarrow`、`lancedb`、`mcp`
   - 开发组：`pytest`、`pytest-httpserver`、`ruff`
3. pytest 配置：`--import-mode=importlib`（各目录下的测试文件可以重名）；`testpaths = ["tests"]`。
4. 空包结构：`src/datumoj/{core,crawlers,parsers,store,index,analysis,eval,reference}/__init__.py`，`tests/` 各子目录，`tests/fixtures/README.md`，`sources/README.md`。
5. `Makefile`：`test`、`lint`、`crawl SOURCE= LIMIT=`、`parse`、`index`、`eval`、`mcp`。
6. `.gitignore` 追加 `data/`、`.secrets/`、`.venv/`、`handoff/**/*.zip`、`handoff/**/*.xlsx`。
7. `uv lock` 后执行 `uv run --frozen pytest -q`。这一步要确认空测试集也能通过，并且依赖缓存已经就位，之后 agent 的 checkout 才能离线安装。
8. 删除根目录的旧 `PLAN.md`，提交（不推送）。

### H1 法令样本
- 在主对话里对 e-Gov 法令 API v2 发**有限次数**的真实请求（约 30 次，1 请求/秒）：法令列表 2 页，外加约 25 部法令的现行版全文（XML 格式）和修订信息。
- 选哪些法令：个人信息保护法、行政手续法、地方自治法（大）、补助金适正化法、电子署名法、中小企业基本法、统计法、公文书管理法、建筑基准法施行令（有别表）、带“条の二”的法令、有多个附则的法令等。挑选标准是题目能覆盖、结构有代表性。
- 存到 `tests/fixtures/egov_law/`，写 README.md 说明每个文件的来源 URL（去掉 Key）、抓取时间和用途。故意做一个损坏的 base64 样本和一个含实体声明的恶意 XML。

### H2 M1 验收
- `make crawl SOURCE=jp-egov-law LIMIT=200`，再跑 `parse`、`index`；用 5–10 个真实问题执行 `datumoj search`。
- 重复执行 crawl，确认没有重复下载（304 或增量跳过）。
- 记录到 `docs/acceptance/M1.md`。通过后决定是否全量抓取法令（约 9000 部，按 1 请求/秒约 3 小时）。

### H3 统计与目录样本（M2 开始前）
- `tests/fixtures/ckan_egov/`：`package_search` 两页（缩成每页 5 条）、一次增量接口响应、两个资源文件。
- `tests/fixtures/stat_dashboard/`：沿用旧项目的 `dashboard-metadata-public.json`、`dashboard-population-public.json`，再补一次分批 `getData` 响应。
- `tests/fixtures/estat_files/`：两种版式的真实统计表，各 1–2 个（cp932 CSV 和 XLSX 各至少一个）。
- `tests/fixtures/tabular/`：旧项目踩过坑的样本（cp932 但响应头写 UTF-8、ZIP 混合编码、伪装成 XLSX 的 ZIP），能复用的从 `local-runtime-evidence/` 里挑，每个来源控制在几 MB 以内。
- `tests/fixtures/area_codes/` 和 `src/datumoj/reference/data/`：总务省“全国地方公共団体コード”最新版。
- `tests/fixtures/jma/`：长期 feed 两次（有新旧条目重叠），外加 3–5 个条目 XML（含旧项目的大阪预报）。

### H4 M2 验收
- 真实抓取 e-Gov 目录（先限制 1000 个数据集）、Dashboard 精选指标、e-Stat 精选表；用 SQL 现场查“都道府県別の人口推移”，并核对 3 个数值和原表一致。
- **开始 JMA 定时轮询**：生成 launchd 任务，每小时执行 `datumoj crawl jp-jma`，日志写到 `data/logs/`。
- 记录到 `docs/acceptance/M2.md`。

### H5 嵌入模型选型（M3 开始前）
- 经你同意后在本机安装 Ollama，拉取 `bge-m3`（约 1.2GB）。
- 在法令小语料上比较“纯关键词”和“关键词 + bge-m3”的 Recall@10，结论写进 `docs/adr/0001-embedding.md`。
- 估算全量嵌入的耗时。法令约 100 万块，本机预计要数小时，需要的话分批夜间跑。

### H6 M3 验收
- 把 `datumoj mcp` 注册进 Claude Code，第一步先调用 `version` 核对加载的版本。
- 现场问 10 个问题，从交接包 06 号文档的验收问题里挑出适用的改写，加上 PLAN 第 8 节 M3 的示例问题。逐条记录问题、工具调用、回答、出处是否属实。
- 记录到 `docs/acceptance/M3.md`。

## 4. 开发任务总表（M0–M3，共 28 个）

验收命令统一是 `/opt/homebrew/bin/uv run --frozen pytest -q <下表的测试路径>`，全部离线。所有任务的 `protected` 都包含 `tests/fixtures/**`（已有样本只读）。

| ID | 标题 | 依赖 | runner / 难度 | 审查 | 验收 |
|---|---|---|---|---|---|
| **M0 地基** ||||||
| IVY-001 | 核心数据模型与 manifest（SQLite） | — | claude / high | human | `tests/core/test_models.py`、`tests/core/test_manifest.py` |
| IVY-002 | 按内容寻址的 raw 存储 | 001 | codex / medium | other_family | `tests/core/test_rawstore.py` |
| IVY-003 | 礼貌 HTTP 客户端：限速、条件请求、重试、上限、去 Key、回放 | 001 | claude / high | other_family | `tests/core/test_http.py` |
| IVY-004 | 来源注册表 schema 与校验 | 001 | claude / medium | other_family | `tests/core/test_registry.py` |
| IVY-005 | 日文文本归一化与编码探测 | — | codex / medium | other_family | `tests/parsers/test_ja_text.py` |
| IVY-006 | 采集基类：计划→抓取→落库→记录，带上限 | 002, 003, 004 | claude / high | other_family | `tests/crawlers/test_base.py` |
| **M1 法令链路** ||||||
| IVY-007 | e-Gov 法令采集器 | 006 | claude / medium | other_family | `tests/crawlers/test_egov_law.py` |
| IVY-008 | 法令 XML 解析：按条切分，带层级和 locator | 001, 005 | codex / medium | other_family | `tests/parsers/test_xml_law.py` |
| IVY-009 | chunk 存储与 FTS5 trigram 关键词索引 | 001 | claude / medium | other_family | `tests/store/test_chunks.py`、`tests/index/test_fts.py` |
| IVY-010 | 解析与索引流水线（增量） | 007, 008, 009 | claude / medium | other_family | `tests/test_pipeline.py` |
| IVY-011 | 命令行：crawl / parse / index / search / status | 010 | claude / medium | other_family | `tests/test_cli.py` |
| IVY-012 | 评测框架：题库格式、执行器、检索指标 | 009 | codex / medium | other_family | `tests/eval/` |
| IVY-013 | 首批法令评测题 20 道 + 评测小语料 | 011, 012 | claude / medium | human | `tests/eval/test_law_eval.py` |
| **M2 目录与统计** ||||||
| IVY-014 | 通用 CKAN 采集器（e-Gov データポータル） | 006 | claude / medium | other_family | `tests/crawlers/test_ckan.py` |
| IVY-015 | 数据集目录存储与目录检索 | 009, 014 | codex / medium | other_family | `tests/store/test_catalog.py` |
| IVY-016 | 表格解析：CSV / XLSX / ZIP，cp932，注记 | 005 | codex / high | other_family | `tests/parsers/test_tabular.py` |
| IVY-017 | 地区码表：全国地方公共団体コード | 005 | codex / medium | other_family | `tests/reference/` |
| IVY-018 | DuckDB 观测值模型、Parquet 落盘与只读查询 | 001 | claude / high | human | `tests/store/test_observations.py` |
| IVY-019 | 统计 Dashboard 采集器（分批规划）与观测值转换 | 006, 015, 017, 018 | claude / medium | other_family | `tests/crawlers/test_stat_dashboard.py` |
| IVY-020 | e-Stat 统计表文件采集器与两类典型表转换 | 006, 016, 017, 018 | claude / high | other_family | `tests/crawlers/test_estat_files.py` |
| IVY-021 | JMA 长期 feed 轮询采集器 | 010 | codex / medium | other_family | `tests/crawlers/test_jma_feed.py` |
| IVY-022 | 统计与目录评测题 20 道 | 013, 015, 019, 020 | claude / medium | human | `tests/eval/` |
| **M3 AI 分析入口** ||||||
| IVY-023 | 向量索引：嵌入接口与 LanceDB 存储 | 009 | claude / medium | other_family | `tests/index/test_vector.py` |
| IVY-024 | 混合检索（RRF）与四种缺口状态 | 023, 015 | codex / high | other_family | `tests/index/test_hybrid.py` |
| IVY-025 | 分析工具层：search / catalog / sql / chunk / cite / version | 024, 018 | claude / high | human | `tests/analysis/test_tools.py` |
| IVY-026 | MCP 服务器（stdio） | 025 | claude / medium | other_family | `tests/analysis/test_mcp_server.py` |
| IVY-027 | 评测补全：数值、缺口、引用、注入，以及门槛命令 | 022, 025 | codex / medium | other_family | `tests/eval/` |
| IVY-028 | 评测题扩到 60 题以上 + 回答规则 skill | 027 | claude / medium | human | `tests/eval/test_full_eval.py` |

**为什么这样分配：**
- **审查**：`human` 共 6 个，分两类：
  - 定接口的：001 数据模型、018 观测值模型、025 工具层。改起来牵连最广。
  - 写评测题的：013、022、028。题目对不对，机器判断不了。
  - 其余由另一家模型自动审查后合并。
- **runner**：claude 和 codex 大致对半分。纯算法、边界多的（raw 存储、日文处理、表格、地区码、RRF）给 codex；需要通盘设计的（数据模型、HTTP、采集基类、工具层）给 claude。异源审查会自动派另一家来审。
- **难度**：high 共 8 个，都是一旦出错会污染下游的地基或安全相关模块。

## 5. 各任务的关键约定（跨任务一致性）

- **locator 格式**（IVY-001 定义）：`key=value` 用 `;` 连接，例如 `art=27;para=2`、`page=3`、`sheet=表1;cell=B4`、`row=12`、`entry=<id>`。
- **doc_key**：跨次抓取稳定的文档键，`<source_id>:<来源内 ID>`，例如 `jp-egov-law:415AC0000000057`。评测题按 `doc_key` + locator 前缀判定命中。
- **采集器注册**：`src/datumoj/crawlers/__init__.py` 的 `CRAWLERS`；**解析器注册**：`src/datumoj/parsers/registry.py`。后续任务只改这两个登记文件，不改别人的模块。
- **Observation 必填 `survey`**：不同调查不能混在同一张表里。注记（“-”“…”“x”“秘匿”）进 `note`，`value` 为空，绝不补零。
- **来源文本标注为数据**：工具输出里来源正文只放在 `content` 字段，并带 `content_role: "untrusted_source_text"`。

## 6. e-Stat 第一批主题（默认清单，可改）

IVY-020 的精选清单先覆盖下面这些，具体 `statInfId` 在 H3 选样本时确定：

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

## 7. M4–M5（只列方向，到时再细拆）

拆细的时间点是 M3 验收之后。到那时数据模型和工具层已经稳定，拆出来的任务更准。

| 里程碑 | 预计任务 |
|---|---|
| M4 日本扩面与增量 | KKJ 按日期分段采集器；PDF 解析（按页 locator，扫描件标 `needs_ocr`）；个人信息脱敏（担当者姓名、电话、邮箱）；BODIK（复用 CKAN，加 DataStore 读取）；jp-gov-web 定向采集（种子清单、深度和域名限制、robots、SPA 外壳识别）；HTML 正文抽取与切块；增量调度（launchd 生成器）；变化检测与每周变化报告；评测加 M4 来源题 |
| M5 欧美 | CKAN 多语言适配（data.europa.eu、英德法荷）；Eurostat SDMX 解析；Federal Register 采集器；World Bank WDI；多语言检索评测 |
