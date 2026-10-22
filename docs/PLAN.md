# DATUMOJ 的计划

> 计划第 3 版（2026-10-20）：改为**契约先行、7 条开发线并行**，每个版本用家纹作代号，当前版本是 **0.1 IVY（蔦）**（第 8 节）。第 2 版起，第 13 节的待定问题已按默认答案定下。任务拆分见 `docs/TASKS.md`。任务编号 `DATUMOJ-IVY-<序号>`，由调度器（orchestrator）派给 agent 执行。
> 2026-10-22 修订：并入团队开源初始化包（PR #5：`AGENTS.md`、`CONTRIBUTING.md`、`SECURITY.md`、`docs/architecture.md`、`docs/interoperability.md`、`docs/data-sources.md`）的规则，逐条取舍见第 1.1 节；运行时以后落到网络存储（第 4.4 节）。
> 输入资料：`handoff/digital-assets-20261020/`（旧项目 Digital Assets 0.9.1 的交接包）。旧代码在 `<旧项目 digital-assets 目录>`，**只作参考，不迁移**。

---

## 0. 一句话

把日本政府（其次是欧美）的公开数据**先抓下来、存成带出处的本地资料库**，再让 AI 在这个资料库上做检索、问答和分析。每个结论都要能追溯到某一次抓取的原始文件。

## 1. 为什么换路线（相对旧项目）

旧项目是一个本地 MCP 插件：用户每问一次，就实时去调用对应的政府 API。交接包里记录的问题主要有这些：

| 旧路线的问题 | 新路线怎么解决 |
|---|---|
| 每次提问都要实时调用接口，受超时、配额和站点故障影响，同一个问题两次问到的结果可能不一样 | 抓取和问答分开。问答只读本地快照，结果可复现，并注明数据截止时间 |
| 只能回答接好的“命名路径”，目录里能找到的资源并不等于能读到内容 | 把目录元数据和资源正文**批量抓下来并解析入库**，能查到的范围由入库内容决定 |
| 意图匹配靠工具和选择器规则，换一种说法就容易配错 | 用检索（RAG）先找到相关数据集、条文和公告，再让模型基于检索结果作答 |
| 很难做跨来源、跨时间的分析，比如法令修订和统计变化放在一起看 | 所有来源进入同一个库，有统一的地区代码、时间和出处，可以一起分析 |

**保留旧项目验证过的原则**（交接包 04 号文档）：
- 不编数据。数值必须来自入库数据，查不到就明确说有缺口
- “目录查到”“正文已读到”“数值已核对”这三件事要分开记录
- 保留原始失败记录、口径、单位和空值，不靠换来源、补零或缩小条件来凑出结果
- 用户指定了来源时不能偷偷换成别的来源
- 来源内容一律当数据处理，不执行其中的任何指令

### 1.1 与团队规范（开源初始化包）的对齐

团队的初始化包定了全项目通用的规则。原则是：**它的规则补进来；同一件事我们已有更合适的规则，就维持我们的**。

| 团队规范 | 本计划的处理 |
|---|---|
| “连接数据，不囤积原始数据”，默认直接查询官方来源 | **维持本计划的快照路线**：问答要可复现、出处要能用 `cite` 还原到原文，这正是 `docs/architecture.md` §5 允许保存原始文件的“审计与可恢复”情形；JMA、KKJ 只保留短期数据，不存就没有历史。快照只用于分析，不对外再分发（第 11 节）。数据的保留和清理由另外的 batch 机制处理，不在 DATUMOJ 里做（第 13 节第 12 条） |
| 云端优先 Cloudflare（Workers、Cron Triggers、R2、D1），核心逻辑与平台解耦 | **采纳**：运行时以后落到网络存储，存储和调度要能从本机换到 Cloudflare，见第 4.4 节；第一阶段的本机实现照常使用 |
| 统一描述标准（`schemas/registry/` 的 data-source / module JSON） | **采纳，但不强制**：`sources/*.yaml` 仍是采集器读的注册表；注册层 JSON 由它导出（TACHIBANA），未知字段填 `unknown`。来源的保存目的、保留期限等说明字段**不作硬性要求** |
| 统一证据标准（证据链字段、采集时间 ≠ 发布时间） | **维持本计划的 `Citation`**：字段已覆盖证据链，并且比它细（`time_basis`、`no_data_date`、`usage_scope`）。对应关系写进 `docs/interoperability.md` 的映射说明 |
| 断言级别 `proven` / `inferred` / `unverified`；关系断言只证明来源文件载明的关系 | **采纳**：写进回答规则（第 4.3 节第 8 条） |
| 错误要区分：来源不可用 / 频率受限 / 认证失败 / 数据格式变化，并标明能否重试 | **采纳**：抓取失败按这四类记录，监控按类提醒（第 5 节抓取守则） |
| 分支 + PR + CI + 人工审查，不直接推 `main`；AI 产出同样要审 | 与本计划一致（第 8 节、`docs/plans/0.2-tachibana.md` 第 0 节） |
| CI（JSON/YAML/Schema 校验、密钥扫描，只读权限） | **采纳并补充**：加跑 pytest，Python 版本对齐 3.12（TACHIBANA H0） |
| `AGENTS.md` 的任务结束报告格式 | 调度器派发的任务**维持调度器的任务包和报告格式**；其余场景按 `AGENTS.md` |
| 测试用录制或模拟数据，不高频请求真实政府网站 | 与本计划一致（`tests/fixtures/`，测试全部离线） |
| 密钥：`.env` 不提交，`.env.example` 只放虚构值；云端凭证用平台的 Secrets | **采纳**：Cloudflare 凭证按 `.env`；数据源的 API Key 维持放在 `.secrets/`（第 11 节） |
| 模块目录 `connectors/`、`skills/`、`catalog/` 等，首个模块落地时再建 | 采集器维持在 `src/datumoj/crawlers/`；导出的注册层 JSON 放 `catalog/`（第 6 节） |

## 2. 目标

**最终形态**：一个可以持续更新的本地“政府数据资料库”，外加一个 AI 分析入口。用户用日语、中文或英语提问，系统能：
1. 找到相关的数据集、统计表、法令条文、公告和报告；
2. 对统计数据**用 SQL 做确定性计算**，不让模型自己估数；
3. 对文本资料（法令、公告、PDF）检索后归纳；
4. 回答里附上出处：来源、URL、抓取时间、快照哈希、定位信息（第几条、第几页或第几行）；
5. 能定期输出“变化分析”，例如本周新增的采购公告、修订的法令、更新的统计表。

**第一阶段完成的标准**（M3 检查点，即 IVY 版本完成时）：
- 日本 4 类来源已入库并能增量更新：法令、e-Gov 数据目录、统计（Dashboard 和精选的 e-Stat 表）、气象厅防灾 XML（每小时定时轮询）。采购公告（KKJ）在拆任务时移出 IVY（2026-10-21 修订），2026-10-22 讨论后也移出 TACHIBANA，以后再定
- 每个抓取来源都可以在本机单独开关（`datumoj schedule`、管理页面 `datumoj admin`），新机器默认全部关闭
- 有一套离线评测集（至少 60 题），检索命中率和数值准确率达到第 10 节的门槛
- 在 Claude Code 或 Claude Desktop 里通过 MCP 提问，能拿到带出处的回答；不支持的问题会说清缺口

## 3. 不做

- **不做实时查询**。问答只读本地库，回答一律标注数据截止时间
- **不做全站镜像**。每个来源都有明确的抓取范围清单（白名单），不做“把某站点全部 API 都抓一遍”这种没有边界的任务
- **不对数值做 RAG**。统计数字只能通过 SQL 查询得到，不能从文本块里“检索出一个数”
- 第一阶段不做网页前端、不做公开部署、不做收费，也不上架插件市场。运行时以后会落到网络存储（Cloudflare R2 等，见第 4.4 节），那是存储位置的变化，不是对外公开数据
- 不判断个案的法律问题、补贴资格、诊断或投资收益
- 不采集个人信息。公告里出现的担当者姓名、电话等，入库前脱敏或不作为检索字段
- 旧项目的 20 国范围先收缩：中国、俄罗斯、土耳其、印度等暂不做；欧美放到 M5
- 不继续维护旧的 Node MCP 插件

## 4. 总体架构

```
            ┌──────── sources/*.yaml 来源注册表（范围、许可、频率、抓取策略）────────┐
            ▼                                                                        │
 ① 抓取 crawl ──► ② 原始库 raw（按内容哈希存放，不可变，manifest 记录每次抓取）       │
                          │                                                          │
                          ▼                                                          │
                 ③ 解析 parse（按格式分流）                                          │
             ┌────────────┴─────────────┐                                            │
             ▼                          ▼                                            │
   ④a 结构化轨道                ④b 文档轨道                                          │
   表格/统计 → Parquet+DuckDB   法令/公告/PDF/HTML/数据集描述 → 文本块 chunks         │
   （维度、单位、地区码、时间）  （按条、页、节切分，带定位信息）                       │
             │                          │                                            │
             │                          ▼                                            │
             │                 ⑤ 索引：关键词（日文 n-gram）+ 向量，混合检索          │
             └────────────┬─────────────┘                                            │
                          ▼                                                          │
         ⑥ 分析层：工具化的 AI（search_docs / search_catalog / query_sql / cite）      │
              入口：MCP 服务器（主）+ CLI；批量报告（变化分析）                         │
                          ▼                                                          │
         ⑦ 评测：离线问答集，衡量检索召回、出处正确、数值精确 ◄──────────────────────┘
```

### 4.1 核心数据模型

```
Source（来源）─< Dataset（数据集/法令/公告）─< Resource（资源文件/端点）
     Resource ─< Snapshot（一次抓取：url、时间、HTTP 状态、sha256、许可、大小）
     Snapshot ─< Document（解析后的文本）─< Chunk（检索单元，带 locator）
     Snapshot ─< Table（解析后的表）─< Observation（维度、时间、地区、单位、值、注记）
Citation = source_id + resource_url + snapshot_sha256 + fetched_at + locator
```
- 原始文件放在 `data/raw/<sha256 前两位>/<sha256>`，内容相同的只存一份
- `manifest`（SQLite）记录每次抓取，包括失败的抓取
- 派生数据（Parquet、索引）都能从 raw 重新生成，所以只备份 raw 和 manifest
- raw 只通过 `RawStore` 接口读写，存放位置可以从本机目录换成网络存储（第 4.4 节）

### 4.2 技术选型（第一阶段默认，用 ADR 记录，可以推翻）

| 层 | 选择 | 理由 |
|---|---|---|
| 语言与环境 | Python 3.12+，用 `uv` 管理依赖 | 数据、PDF 和日文处理的生态最全；调度器验收原生支持 pytest |
| HTTP | httpx + 自写的限速、重试和条件请求（ETag / If-Modified-Since） | 对政府站点要“礼貌”，增量抓取靠条件请求 |
| 结构化存储 | DuckDB + Parquet | 单机、零运维、SQL 能力强，适合给模型做确定性计算 |
| 元数据与 manifest | SQLite | 简单可靠，有事务 |
| 文档解析 | lxml（法令 XML、JMA XML），pypdfium2 和 pdfplumber（PDF），openpyxl，charset 检测（cp932/Shift_JIS） | 避免 AGPL 许可的 PyMuPDF |
| 日文处理 | NFKC 归一化、全半角统一、和历转西历；分词用 SudachiPy（可选） | 政府数据里全角数字和和历（令和、平成）非常多 |
| 关键词检索 | SQLite FTS5 trigram（日文不分词也能搜） | 零依赖；需要时再换 tantivy |
| 向量检索 | LanceDB，嵌入模型可替换 | 法令切块后约 100 万块，需要 ANN 索引，sqlite-vec 暴力检索撑不住；默认 bge-m3 通过本机 Ollama 运行，H5 在评测集上验证效果 |
| 分析层 LLM | 第一阶段只通过 MCP 由宿主模型（Claude）调用工具，代码里不调用任何 LLM | 延续旧项目“不额外付费调用模型”的决定；查询改写由宿主模型做，代码只提供中英日术语对照表 |
| 调度 | `make crawl` 加系统定时任务（macOS 用 launchd，Linux 用 cron 或 systemd timer） | 第一阶段不上云；定时配置不写死某台机器。以后可换成 Cloudflare Cron Triggers（第 4.4 节） |

### 4.3 检索与问答的关键规则

1. **先找资料再回答**：模型先调用 `search_catalog` 或 `search_docs` 找候选，再按需调用 `query_sql` 或 `get_chunk`。最终回答只能使用工具返回的内容。
2. **数值必须走 SQL**：`query_sql` 只读，有超时和行数上限，返回结果会附带它用到的 Observation 出处。
3. **出处要可验证**：每条引用都能用 `cite` 工具还原到快照里的原文片段。评测会抽查引用是否真的支持对应的结论。
4. **时间语义**：回答要区分统计期间、发布日、施行日和抓取日，不能把“截至某日的快照”说成“现在”。每条出处（Citation）都带时间口径 `time_basis`；来源不公布数据日期时（例如国土地理院标高）明确写“无数据日期”，**不能拿抓取日顶替**。法令要说明按哪天的施行状态，并提示已公布但尚未施行的修改。
5. **说明缺口**：库里没有就明确说没有，并给出离它最近的可查内容。不能拿别的来源顶替。要区分四种状态：**检索零命中**、**来源没有入库**、**抓取失败**、**超出范围**（旧项目把它们混在一起，用户分不清）。
6. **跨语言检索**：用中文或英文提问时，先把查询改写成日文再做关键词检索，向量检索用多语言模型。旧项目直接拿中文词查日本来源，结果零命中。
7. **多轮检索**：复杂问题要拆成几个子查询，分别检索后再汇总。旧项目只查一次，问“大阪 AI 创业政策”时就退回到了全国法令检索。
8. **断言级别**（团队规范）：回答里的结论分三级——`proven`（引用的原文直接证明）、`inferred`（由多条证据推导，要写出依据）、`unverified`（待验证）。关系类结论只说来源文件载明的关系：例如采购公告只能证明“这份公告载明的采购关系”，不能外推出股权、控制或合作关系。

### 4.4 运行环境与网络存储（2026-10-22 新增）

- **方向**：运行时以后落到网络存储，云端优先 Cloudflare（R2 存原始文件和派生文件，Workers / Cron Triggers 跑定时抓取，D1 等按需选择），与团队规范 `docs/architecture.md` §4 一致。
- **做法**：核心逻辑与平台解耦。所有存储经接口访问（`RawStore` 已有；manifest、chunk、目录、观测值、向量各有 Protocol），数据位置只由一处配置决定；平台相关的实现放在独立的适配层，本机实现继续可用。
- **分步**：TACHIBANA 先把存储位置收拢到一处配置，并给 `RawStore` 加 S3 兼容（R2）实现，用契约测试套件在模拟存储上验证；SQLite 类的库（manifest、目录、chunk）上网络存储的方式（D1，或只同步备份）用 ADR 决定。2026-10-22 已定（ADR 0002）：索引跟随数据上 R2，本机的库按需从 R2 重建；同步频率按来源分别设置。真正部署到 Cloudflare，等账号、凭证和部署流程定下来以后另排版本。
- **凭证**：R2 / Cloudflare 凭证只放 `.env` 或平台 Secrets，不进仓库；生产资源的创建和变更要人明确授权（`SECURITY.md`）。

## 5. 来源范围与优先级

只列第一批，完整清单由 `sources/*.yaml` 维护，每个来源一个文件。

### 日本（主）

| 优先级 | ID | 来源 | 抓取方式 | 进入哪条轨道 | 备注 |
|---|---|---|---|---|---|
| P0 | jp-egov-law | e-Gov 法令 API v2 | 全量法令列表 + 每部法令现行版 XML；增量要逐部比对修订历史（API 没有全局的“按更新时间筛选”）；先核实是否有官方全量 XML 打包下载 | 文档（按条切分） | 约 9000 部；体量适中、价值高、结构清晰，**适合打通第一条链路**。正文是 JSON 里的 base64 编码 XML；关键词接口的 limit 计的是命中位置数，不是法令数 |
| P0 | jp-egov-data | e-Gov データポータル（CKAN） | 全量目录元数据，用 `recently_changed_packages_activity_list` 做增量，资源文件按白名单下载 | 目录 + 两条轨道 | CKAN 采集器写好后可复用到 BODIK 和欧美各门户；许可分数据集和资源两级，经常是 unknown 或互相矛盾，要原样记录 |
| P0 | jp-stat-dashboard | 统计 Dashboard API | 指标目录 + 精选指标的全地区时间序列 | 结构化 | 无需 Key；getData 每次最多 5 个指标、50 个地区，而且**没有分页**，需要分批规划；可以用 `modifiedFrom` 做增量 |
| P1 | jp-estat-files | e-Stat 统计表文件（CSV / XLSX） | 精选表清单（按 statInfId 下载文件） | 结构化 | 旧项目实际可用的是这条路径；文件多为 cp932，响应头即使写着 UTF-8 也不能信 |
| P2 | jp-estat-api | e-Stat API v3 | 统计表目录全量；数值只抓精选清单 | 结构化 | 需要 appId。旧项目读取凭据连续失败后被用户叫停，**要先在主对话里重新核实 appId 能用**再排任务；单次调用最多 10 万个单元格 |
| P1 | jp-kkj | 官公需情报门户 KKJ | **没有分页**（每次最多 1000 条），按 `CFT_Issue_Date` 分日期段采集；再下载关联的 go.jp / lg.jp 文本 PDF | 文档 | 全文检索会把“システム”这类泛词匹配得太多，所以采集时不按关键词筛；担当者信息要脱敏。**2026-10-22 讨论后移出 TACHIBANA，以后再排** |
| P1 | jp-gov-web | 省厅和自治体政策网页（定向） | 种子 URL 清单，限定深度和域名（go.jp / lg.jp / metro.tokyo 等），抓 HTML 和 PDF | 文档 | **直接对应旧项目验收失败的场景**：问“大阪 AI 创业政策”时没有任何来源可查。只抓白名单种子，不做全网爬取 |
| P1 | jp-bodik | BODIK ODCS（CKAN） | 复用 CKAN 采集器；DataStore 读取按 `_id` 排序，offset 按实际返回的行数推进 | 目录 + 两条轨道 | 资源逐个核对许可（多为 CC BY 4.0） |
| P1 | jp-jma | 气象厅 XML（Atom feed）和平年值 | 轮询长期 feed（每小时更新，**只保留约 7 天**） | 文档 + 结构化 | 想要历史就必须**尽早开始持续轮询**，所以采集器排在 M2 末尾而不是 M4；不做实时预报 |
| P2 | jp-ksj / jp-census / jp-mhlw / jp-abr | 国土数值信息 S12、2024 经济普查、医疗开放数据、ABR 地址 | 命名文件下载 | 结构化 | 旧项目已经核验过这些文件，可以优先做；ZIP 里会混有 SJIS 和 UTF-8 两种编码的成员 |

### 欧美（次，M5）

| ID | 来源 | 抓取方式 |
|---|---|---|
| eu-data-europa | data.europa.eu（CKAN 风格 / DCAT） | 目录元数据 |
| eu-eurostat | Eurostat SDMX API | 精选数据集 |
| uk / de / fr / nl | data.gov.uk、GovData、data.gouv.fr、data.overheid.nl（CKAN 或类似接口） | 复用 CKAN 采集器 |
| us-fedreg | Federal Register API | 增量抓取公报全文 |
| us-govinfo / us-datagov | GovInfo、Data.gov（需要 api.data.gov Key；GovInfo API 文档：https://github.com/usgpo/api） | 等 Key 到位后再做 |
| intl-wdi | 世界银行 WDI | 精选指标做国际比较基准 |

### 抓取守则（写进代码和测试）
- 每个来源单独配置限速（默认 1 请求/秒），User-Agent 写明项目名和联系方式，遵守 robots.txt
- 用条件请求避免重复下载；失败按指数退避，重试有上限；失败要写进 manifest，不能静默跳过
- 每个 Resource 都记录许可，回答时附上署名。许可**不能一概假定**为“公共データ利用規約 1.0”（例如国土地理院的数据可能涉及測量法），每个来源在注册表里记录 `usage_scope` 和“可否再分发”，逐个核对；核对完之前一律 `internal_only`
- 单个来源设容量上限，超出就停下并报告，不能无边界地抓
- 失败按类型记录：**来源不可用**、**频率受限**、**认证失败**、**数据格式变化**，并标明能否重试；调用方据此决定重试、退避或提醒（团队规范 `docs/data-sources.md` §4）
- 来源注册表里的说明字段（保存目的、保留期限、更新机制等）能填就填，**不作硬性要求**；不知道的填 `unknown`，不编造

## 6. 目录结构

```
datumoj/
├── pyproject.toml / uv.lock / Makefile
├── sources/                 # 来源注册表 *.yaml（范围白名单、许可、频率）
├── catalog/                 # 由 sources/*.yaml 导出的注册层描述 JSON（团队描述标准，TACHIBANA 起）
├── schemas/                 # 内部数据契约 *.schema.json（模型导出）＋ registry/ 注册层标准
├── examples/                # 团队规范的最小真实示例
├── src/datumoj/
│   ├── core/                # 配置、数据模型、manifest、raw 存储、HTTP 客户端
│   ├── crawlers/            # 按来源类型分：ckan.py、egov_law.py、estat.py、kkj.py …
│   ├── parsers/             # xml_law.py、pdf.py、tabular.py（csv/xlsx/cp932）、ja_text.py
│   ├── store/               # duckdb 表模型、chunk 存储
│   ├── index/               # fts.py、vector.py、hybrid.py（RRF 融合）
│   ├── analysis/            # 工具函数（search/query/cite）、mcp_server.py、cli.py、reports/
│   └── eval/                # 评测执行器与指标
├── tests/
│   ├── fixtures/<source>/   # 录制好的真实响应（小样本），测试全部离线
│   └── …
├── eval/questions/*.yaml    # 评测题库
├── data/                    # raw/、parquet/、index/（不进 git）
├── docs/                    # PLAN.md、architecture.md、interoperability.md、data-sources.md、adr/、plans/、tasks/、acceptance/
├── docs/references/        # 外部提供的调研资料和参考样本（规范见该目录 README）
├── handoff/                 # 旧项目交接包（只读参考）
├── .github/                 # CI 与 Issue / PR 模板
├── AGENTS.md / CONTRIBUTING.md / SECURITY.md   # 团队协作规范
└── .secrets/                # API Key（不进 git）
```

## 7. 怎么并行开发

### 7.1 原则

1. **契约先行**。第一步只定两样东西：
   - 数据模型（`core/models.py`，导出 JSON Schema 到 `schemas/`）；
   - 接口协议（`core/contracts.py`），每个协议配一个假实现和一套“合格测试”（`datumoj/testing/`）。

   契约由人审查。契约定下之后，各条线**只依赖契约，不 import 其他线的实现**。

2. **假实现解耦**。各线开发时用假实现替代别人的模块。例如入口线可以先用 FakeRetriever 把工具层和 MCP 做通，不用等检索线完成。真实实现必须通过和假实现同一套合格测试，这样替换时不会出意外。

3. **插件自动发现，没有共享登记文件**。采集器和解析器各自一个文件，声明自己的 `kind`，由 `discover_crawlers()` 和 `discover_parsers()` 扫描加载。新增来源只是新建文件，两条线不会改到同一个文件。

4. **集成任务单独做**。每个版本有一个集成任务，负责把真实实现接起来（只改 `wiring.py`、`cli.py`、`sources/`），并在 fixture 语料上跑评测门槛。集成时发现的模块 bug，开修复任务交回对应的开发线，不在集成任务里顺手改。

5. **调度器配置**。本项目的并发上限是 3（`max_concurrent: 3`），每个任务预留 2GB 内存。

### 7.2 七条开发线与负责人

团队按 3–4 人设计。每条线有一位**负责人（owner）**，职责有三项：
- 审本线需要人审查的任务；
- 录制本线来源的测试样本（H1）；
- 跑本线的真实抓取和验收。

具体人选待定，先用甲乙丙丁占位：

| 线 | 内容 | 拥有的目录 | 负责人（4 人时） | 3 人时 |
|---|---|---|---|---|
| 契约 | 数据模型、接口协议、假实现、日文文本工具 | `core/models.py`、`core/contracts.py`、`testing/`、`text/` | 项目负责人（Aaron） | 同左 |
| A 抓取基础 | raw 存储、manifest、HTTP、注册表、执行器 | `core/` 其余文件 | 甲 | 甲 |
| B 文档线 | 法令、JMA（M4 起加 KKJ、PDF、网页） | `crawlers/` 和 `parsers/` 下的对应文件 | 甲 | 甲 |
| C 统计线 | 表格、地区码、观测值库、Dashboard、e-Stat | `tabular/`、`reference/`、`store/observations.py` | 乙 | 乙 |
| D 目录线 | CKAN、目录存储与检索 | `crawlers/ckan.py`、`store/catalog.py` | 乙 | 乙 |
| E 检索线 | FTS、向量、混合检索、缺口状态 | `index/`、`store/chunks.py` | 丙 | 丙 |
| F 入口线 | 流水线、CLI、工具层、MCP | `pipeline.py`、`cli.py`、`analysis/`、`mcp_server.py` | 丙 | 丙 |
| G 评测线 | 评测框架、题库、回答规则 skill | `eval/`、`skills/` | 丁 | 项目负责人 |

评测线最好由**不写检索代码的人**负责。出题的人和被考的代码分开，题目才不会无意中迁就实现。

### 7.3 调度器规则（不变）

1. **验收全部离线**：agent 在全新的 checkout 里只能运行 pytest，测试用 `tests/fixtures/` 下的录制样本。
2. **真实抓取不在任务里做**，由负责人在主对话里执行，结果记录到验收文档。
3. **依赖统一管理**：agent 不能运行 `uv`。依赖在 H0 一次锁定；之后新增依赖，在主对话里改好、提交，再派任务。
4. **验收命令**：`make check T=tests/<...>`。Makefile 先在 PATH 里找 `uv`，找不到再用本机路径，这样换机器、换人也能跑；`check` 目标等价于 `uv run --frozen pytest -q $(T)`。
5. **scope 要窄**：契约文件对所有开发线任务都是 protected；需要改契约就停下来问，由人开契约修订任务。
6. **审查**：默认 `other_family`。契约、观测值库、工具层、评测题和集成任务由人审查。

### 7.4 团队怎么审核

- **计划和任务**：任务文件的副本在 `docs/tasks/`，通过 GitHub PR 审核。审核通过后，主对话把它们复制到 `.orch/tasks/` 并同步给调度器。
- **任务结果**：调度器跑在项目负责人的机器上。需要人审查的任务进入 review 状态后，主对话把该任务的分支推到 GitHub，由对应线的负责人在 PR 里审核；审核结论回传后，由项目负责人执行 `bin/orch approve` 或 `reject`。

## 8. 版本、代号与里程碑

### 8.1 规则

- **项目编号**固定为 `DATUMOJ`，以后不变。
- **每个版本取一个家纹作代号**（英文名，调度器里登记为代号系列 `Kamon` / 家纹），不要求按首字母排序；进入新版本时，从没用过的家纹里挑一个。
- **任务编号跟着版本走**：`DATUMOJ-<代号>-<序号>`。例如 0.1 版的任务是 `DATUMOJ-IVY-101`。序号按开发线分号段：
  - 契约 001–009
  - A 线 1xx、B 线 2xx、C 线 3xx、D 线 4xx、E 线 5xx、F 线 6xx、G 线 7xx
  - 集成 9xx
- **一个版本对应一整轮计划**。版本内部的里程碑只是检查点，不另起代号。
- **tag**：
  - 版本完成后打 `v<版本>-<代号小写>`，例如 `v0.1.0-ivy`；
  - 版本内的检查点打 `v<版本>-<代号小写>-m<N>`，例如 `v0.1.0-ivy-m1`。
  - 里程碑记录：计划合并、任务拆分、检查点、版本发布等节点，由主对话更新 `plan/milestones.json`（渲染到 https://plan.datumoj.com/ ），单独提 PR 进 `main`；**对应的 PR 合并进 `main` 才算完成**。规则见 `CLAUDE.md`。
  - 打 tag 的条件：对应的集成任务合并，并且人工验收通过（真实抓取一次，再用 5–10 个真实问题现场问答，记录到 `docs/acceptance/<tag>.md`）。tag 打在 `main` 上，由项目负责人推送。

### 8.2 版本表

| 版本 | 代号 | 范围 | 状态 |
|---|---|---|---|
| **0.1** | **IVY（蔦）** | 契约 → 法令链路 → 目录与统计 → AI 分析入口（第一阶段） | **已完成**（2026-10-21，tag `v0.1.0-ivy`），48 个任务，验收见 `docs/acceptance/` |
| 0.2 | **TACHIBANA（橘）** | 日本扩面与持续运营：省厅和自治体政策网页与 PDF、BODIK、抓取监控、每周变化报告、存储适配层（为网络存储做准备）、注册层描述导出，并补上 IVY 遗留的检索缺口。KKJ 采购公告移出本版本（2026-10-22） | **开发完成**，已合并进 `main`（#15，2026-10-22），检查点 m1 已打 tag；H4 真实问答验收待做。计划见 `docs/plans/0.2-tachibana.md` |
| 0.3 | 待定（候选：楓 Maple） | 欧美：data.europa.eu、Eurostat、英德法荷门户、Federal Register；从 0.2 移来（2026-10-22）：在 GitHub Actions 上运行抓取（raw 和索引上 R2、可从 R2 重建、按来源设同步频率，即 ADR 0002 的实现），以及新来源一周试运行观察（原 0.2 H3） | 之后 |

其他候选代号：片喰 Katabami、柏 Oak、桐 Paulownia、藤 Wisteria、葵 Aoi、桔梗 Bellflower、菊 Chrysanthemum。

### 8.3 IVY（0.1）内部的检查点

| 检查点 | tag | 人能看到什么 | 集成任务 |
|---|---|---|---|
| M0 契约与地基 | `v0.1.0-ivy-m0` | 契约审查通过；`make test` 全部通过；用回放样本完整走一遍“计划→抓取→存 raw→写 manifest”，失败和超上限都有记录 | DATUMOJ-IVY-105 |
| M1 法令链路 | `v0.1.0-ivy-m1` | 真实抓取 200 部法令，重复抓取不会重复下载；`datumoj search "個人情報の第三者提供"` 返回条文和出处；法令题 Recall@10 ≥ 0.85 | DATUMOJ-IVY-901 |
| M2 目录与统计 | `v0.1.0-ivy-m2` | 能搜到 e-Gov 目录的数据集；`datumoj sql` 查都道府县人口并附出处；JMA 开始每小时轮询 | DATUMOJ-IVY-902 |
| M3 AI 分析入口 | `v0.1.0-ivy-m3`，H6 验收后打 `v0.1.0-ivy` | 在 Claude 里通过 MCP 问“東京都と大阪府の人口推移を比較して、関連する法改正もあれば”，得到带出处的回答；60 题全部达标。**IVY 完成，第一阶段完成** | DATUMOJ-IVY-903 |

## 9. 旧项目可复用的东西（只借鉴，不迁移代码）

旧代码是 Node/TypeScript，新项目用 Python，所以**借鉴设计和测试样本，不搬代码**。旧仓库的规范版本在 `<旧项目 digital-assets 目录>/repo/`（0.9.1）；同级的 `plugin-prototype/` 是 0.8 的旧副本，不要用。

| 可借鉴的 | 位置（旧仓库 `repo/plugin-prototype/` 下） | 在新项目里的用法 |
|---|---|---|
| 只读 HTTP 客户端的安全设计：公网地址白名单、逐跳校验重定向、容量和时长预算、记录 sha256 | `runtime/src/adapters/http-readonly.ts` | 照着它写 DATUMOJ-IVY-103，**另外补上限速和重试**（旧版故意没有） |
| “原始字节 + 回执”的保存方式 | `live-sources.mjs`、`local-runtime-evidence/` | 就是新 raw 存储 + manifest 的雏形 |
| 安全的 XML 解析（禁用 DTD 和实体、限制大小） | `runtime/src/adapters/jma/secure-xml.ts` | 法令和 JMA 解析器都按这个原则用 lxml（`resolve_entities=False` 等） |
| CSV / XLSX / GeoJSON 解析的边界处理（严格的 cp932、不计算公式、不转换坐标） | `runtime/src/adapters/bodik/{text-files,excel,geojson}.ts` | 表格解析器的测试用例清单 |
| 证据和定位模型（sheet!cell、CSV 行号、feature_index） | `runtime/schemas/app/{evidence,source_locator}.schema.json` | Citation.locator 的格式参考 |
| 各来源的参数、分页和上限说明 | `handoff/.../references/early_research/*_Guide.md` | 写采集器任务时附在背景里 |
| 真实响应样本 | `runtime/tests/fixtures/source-api/*`、`test/fixtures/jma-*.xml`、`local-runtime-evidence/`（1.3GB，挑选使用） | 做 `tests/fixtures/` |
| 20 国来源注册表 | `handoff/.../references/registry/international-sources.json` | M5 欧美来源的种子 |

旧项目**没有**地区码表（都是按用途写死的），需要新建。

### 旧项目踩过的坑（都要写成测试用例）
- 响应头写 UTF-8，实际字节是 cp932（e-Stat 普查 CSV）
- 同一个 ZIP 里同时有 SJIS 和 UTF-8 的成员（国土数值信息 S12）
- ZIP 被误判成 XLSX，要读中央目录才能分辨
- 请求时要求 identity 编码，服务器仍然返回 gzip（巴西 IBGE）
- 网页返回 200，内容却只是 SPA 外壳，必须识别成“没有正文”而不是成功（瑞士 SNB）
- JMA 预报里可选的 `Duration` 字段缺失时，解析器不能报错
- 东京的多个车站几何对象共用同一个车站 ID
- 下载 ID 实际属于别的县（佐贺）：入库时要核对元数据里的地区是否和预期一致
- 2016 年的旧普查数据不能当成现状；不同调查（普查和行政登记）的数据不能相加，也不能直接算增长率

## 10. 质量与评测

- **单元测试**：每个解析器都要用真实样本 fixture 测试，覆盖编码、和历、全角字符、空值和注记（如“-”“…”“x”“秘匿”）
- **评测集** `eval/questions/*.yaml`，每题包含：问题、语言、期望命中的 Resource 或条文、期望的数值（如有）、不应出现的内容
  - 来源一：交接包 06 号文档的验收问题，改写成适合新架构的版本
  - 来源二：每个来源至少 10 题，覆盖检索、数值、时间语义和缺口
- **指标与门槛**（M3 起作为合并门槛）：
  - 检索 Recall@10 ≥ 0.85（评测离线进行，用固定的小语料快照）
  - 数值题精确匹配 = 100%（数值必须来自 SQL）
  - 引用可还原率 ≥ 0.95（检索结果前 3 条都能用 `cite` 还原到快照原文）
  - 数值出现率 = 100%（数值题的期望值必须原样出现在引用的原文片段或数据行里）
  - 缺口题的正确拒答率 = 100%
- **引用支持率**（引用的原文是否真的支持结论）离线无法可靠地机器判断，不作为合并门槛，改为里程碑验收（H6）时人工抽查，目标 ≥ 0.95
- 需要 LLM 作答的端到端评测不放进调度器验收（结果不确定、要花钱），由人在里程碑验收时运行

## 11. 安全、合规与凭据

- 只抓公开数据，遵守各站点的利用规约和 robots.txt；许可信息跟着数据一起存储，回答里附上署名
- API Key 放在 `.secrets/public-data.env`（在 .gitignore 里），日志和 manifest 中的 URL 要去掉 Key
- 外部团队提供的资料放在 `docs/references/`，通过 PR 提交，不能含 Key、个人信息或许可不明的大文件（规范见 `docs/references/README.md`）
- 来源正文可能含有“指令样式”的文本（提示注入），检索结果交给模型时要标注为数据；MCP 工具一律只读
- 个人信息：公告类来源（例如以后接入的 KKJ）里的担当者姓名、电话、邮箱在入库前替换成占位符
- 原始数据只供分析，不再对外分发；放到网络存储以后也是私有存储，不公开访问。公开分享前要逐项核对许可
- Cloudflare / R2 凭证放 `.env`（在 .gitignore 里，`.env.example` 只放虚构值）或平台 Secrets；来自 fork 的 PR 拿不到任何凭证；创建或变更生产资源要人明确授权（`SECURITY.md`）
- 项目许可证尚未确定（候选 MIT / Apache-2.0），由维护者决定后再加 `LICENSE`

## 12. 风险

| 风险 | 影响 | 应对 |
|---|---|---|
| e-Stat 等来源体量巨大 | 磁盘、时间、被限流 | 目录全量，数值只抓精选清单；单来源设容量上限 |
| 站点改版或接口变更 | 采集失败 | 每个采集器都有 fixture 契约测试；真实抓取失败记进 manifest 并告警 |
| 日文检索效果差 | 回答质量低 | trigram 关键词检索兜底，再加多语言向量；用评测集量化选型 |
| PDF 是扫描件 | 抽不出文本 | 第一阶段标记为 `needs_ocr`，跳过并统计；OCR 留到以后 |
| 统计口径混用 | 得出错误结论 | Observation 必须带单位、口径和调查名；不同调查的数据不能直接相加或计算增长率（沿用旧项目规则） |
| agent 任务越界或测试造假 | 质量失控 | scope 收窄，测试先行，测试文件列为 protected，异源审查 |
| 数据过时被当成现状 | 误导 | 回答必须写明截止时间；变化报告定期刷新 |
| JMA、KKJ 等来源只保留短期数据 | 历史缺失，以后补不回来 | 采集器尽早上线并持续轮询；需要一台常开的机器（见第 13 节第 8 条） |
| 宿主不重新加载新版 MCP 服务器（旧项目反复出现） | 验收时测到的是旧版本 | MCP 服务器暴露 `version` 工具；验收第一步先核对版本号 |
| 定向网页采集变成无边界的爬取 | 违反规约、磁盘失控 | 种子白名单，限定深度和域名，遵守 robots.txt，单来源设容量上限 |
| 来源文本里藏有提示注入 | 模型被诱导 | 工具只读；检索结果标注为数据；评测集里放注入样本 |

## 13. 已定的默认答案（2026-10-20，可以随时推翻）

| # | 问题 | 默认答案 |
|---|---|---|
| 1 | 分析层的模型 | 第一阶段只走 MCP 宿主模型，代码不调用 LLM，不产生 API 费用。批量变化报告先用模板生成，不做 LLM 摘要 |
| 2 | 嵌入模型 | bge-m3 本地运行（Ollama），向量库 LanceDB。H5 用评测集验证；效果不够再讨论 API 嵌入模型 |
| 3 | e-Stat 精选表 | 先用 `docs/TASKS.md` 第 6 节的 8 个主题。业务方有具体问题时再调整 |
| 4 | 交接包进 git | 提交 Markdown、CSV、JSON；zip 和 xlsx 在 `.gitignore` 里忽略，只留在本地 |
| 5 | 数据存放位置 | `data/` 放在仓库目录下（不进 git），可以用 `DATUMOJ_DATA_DIR` 改到别处 |
| 6 | 欧美的优先顺序 | EU 整体（data.europa.eu + Eurostat）→ 美国 Federal Register → 英、德、法、荷门户 → 需要 Key 的 GovInfo、Data.gov |
| 7 | 变化报告 | 要。先输出本地 Markdown 到 `data/reports/`，M4 再决定是否推送 |
| 8 | 定时抓取的机器 | 先用项目负责人的 Mac（launchd），配置写成可移植的（换到 Linux 用 cron）；M2 验收时开始 JMA 每小时轮询；中断会留缺口，变化报告里要注明 |
| 9 | e-Stat appId | 第一阶段只用文件下载，不用 API。API 放在 P2，届时在主对话里只发一次有限请求核实 |
| 10 | jp-gov-web 种子清单 | 内阁府、总务省、大阪市，M4 开始前再细化到具体栏目。东京都、大阪府（规约不允许复制转载）和经产省（对爬虫返回 403）2026-10-22 决定不进 0.2 |
| 11 | 旧项目真实样本 | 从 `local-runtime-evidence/` 挑小样本（每个来源几 MB 以内）放进 `tests/fixtures/`，缺的在 H1 和 H3 里补录 |
| 12 | 数据保留与清理（2026-10-22） | DATUMOJ 不做数据清理；保留和清理由另外的 batch 机制处理 |
| 13 | 来源说明字段（2026-10-22） | 保存目的、保留期限等说明不作硬性要求；注册层描述 JSON 由 `sources/*.yaml` 导出 |
