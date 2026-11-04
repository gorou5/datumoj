---
id: DATUMOJ-MAPLE-201
title: Federal Register 采集器与解析：按日期窗口增量、每窗口不超过 10,000 条，全文按节切块
project: DATUMOJ
status: ready
priority: 2
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/fedreg.py", "src/datumoj/rikolto/parsers/fedreg.py", "sources/us-fedreg.yaml", "tests/crawlers/test_fedreg.py", "tests/parsers/test_fedreg.py", "tests/integration/test_fedreg_e2e.py", "catalog/us-fedreg.json", "results/DATUMOJ-MAPLE-201/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/**", "schemas/**", ".github/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/wiring.py", "src/datumoj/eltiro/**", "sources/README.md"]
protected: ["tests/fixtures/**", "tests/regressions/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_fedreg.py
    - cmd: make check T=tests/parsers/test_fedreg.py
    - cmd: make check T=tests/integration/test_fedreg_e2e.py
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-201/summary.md 完成说明（含窗口切分的做法、yaml 每个值的依据）"]
  review: {by: other_family, checklist: ["许可、署名、allowed_hosts、频率和上限是否和来源决策文档一致", "是否只请求白名单主机、遵守 robots，不绕过", "单位、时间、注记是否原样保留，没有补零或自行换算", "是否有联网的测试", "是否改了契约、wiring 或其他来源的实现", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
美国联邦公报（Federal Register）的规则、规则草案和公告能入库、按节检索，回答带文书编号、发布日期和官方链接。

# 背景
来源决策文档 3.2、4.4、6.1–6.4 节；样本 `tests/fixtures/fedreg/`（检索一页、一篇文书的 JSON 和全文 XML）。
- API 实测（决策文档 3.2）：`count` 最多显示 10,000、`total_pages` 最多 50；**`page` 超过 50 不报错，静默返回第 1 页**；`per_page` 最小有效 20、最大 2000。

计划见 `docs/plans/0.3-maple.md`（第 5、6.6 节）和来源决策 `docs/plans/0.3-maple-sources.md`（下文列出的节）。本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- **按插件接入**：`crawlers/` 加一个模块（`Crawler` 子类声明 `kind`），`parsers/` 加一个模块（`Parser` 子类声明 `source_kind`），再加 `sources/<id>.yaml`；`discover_crawlers` / `discover_parsers` 会自动发现，不改 `wiring.py`。写法参照已有的 `stat_dashboard`（API → 观测值）、`ckan`（目录 → Dataset）、`estat_files`（文件 → 观测值）。
- 契约（`core/contracts.py`、`core/models.py`）和 schema 不改；需要改时写进 `summary.md` 并停下来问。
- 样本在 `tests/fixtures/<目录>/`（H1，PR #32 已合并），只读；每个目录的 README 写了 URL、过滤参数、样本类型和许可。测试全部离线，HTTP 只用这些样本或 `pytest-httpserver`。
- yaml 的值按决策文档：`schedule` 见 6.1，`rate_per_sec` / `max_total_bytes` / `max_files` 见 6.2，`allowed_hosts` 见 6.3，许可的 `name` / `url` / `attribution` 见第 4 节；核查完之前一律 `usage_scope: internal_only`、`redistribution: unverified`、`verified_at: null`。来源开关在调度状态里，默认关闭，本任务不打开。
- 不新增依赖，不改 `pyproject.toml`、`uv.lock`；仓库是公开的，不写账号、密钥、本机绝对路径；临时文件放 `tmp_path`。

# 要求
1. **yaml** `sources/us-fedreg.yaml`：`country: us`，`base_url: https://www.federalregister.gov/api/v1/`，`allowed_hosts: [www.federalregister.gov]`，`schedule: daily`；`scope` 写文书类型 `RULE`、`PRORULE`、`NOTICE`，起点 `2026-10-01`，回看天数 7。排除 Public Inspection 和 `PRESDOCU`。
2. **采集**（`kind: fedreg`）：
   - 检索用 `documents.json`，`conditions[type][]` 和 `conditions[publication_date][gte|lte]`；起点取“上次成功运行覆盖到的日期减 7 天”和 `2026-10-01` 中较晚的一个，第一次从 2026-10-01 开始。
   - 按日期窗口切分：某个窗口的 `count` 达到 10,000 或 `total_pages` 达到 50 时，把窗口对半切开重查，直到每个窗口都能完整翻页；单日仍超限时记失败原因，不静默截断。**不请求第 51 页及以后**。
   - 每篇文书再取全文：用 API 返回的官方全文链接（结构化 XML）；链接主机不在 `allowed_hosts` 时跳过并记原因，不自动加白名单。
3. **解析**（`source_kind: fedreg`）：
   - 一篇文书一个 Document，键用文书编号（document number）；`published_at` 用发布日期；官方 HTML 链接、类型、机构、CFR 引用放进元数据。
   - 全文 XML 按节切块，locator 写节标题路径（例如 `SUPPLEMENTARY INFORMATION > II. Background`）；节很长时再按段落切，块不跨节。
   - 检索 JSON 本身不生成重复的块，只用来列文书。
4. **测试**：
   - 解析：样本文书切出的块数、第一个块的节路径、`published_at`、文书编号。
   - 采集：用 `pytest-httpserver` 构造 `count` 超过 10,000 的窗口，断言窗口被切开、从不请求 `page>50`；增量起点按上次成功日期回看 7 天；`PRESDOCU` 不出现在请求里；全文链接主机不在白名单时跳过。
   - `tests/integration/test_fedreg_e2e.py`：用样本离线走一次 抓取 → 解析 → 建索引，用英文关键词检索到样本文书的某一节，引用带文书编号和发布日期。
5. `make lint` 0 条，`make check T=tests` 全部通过。

# 不做
- 不做历史回补（2026-10-01 以前）。
- 不抓网页版（robots 禁止若干网页搜索路径，决策文档 6.4），只用 API。
- 不碰 Public Inspection。

# 补充：来源目录文件（2026-10-31）
- 新来源要同时提交 `catalog/us-fedreg.json`（仓库里登记来源的导出目录，`tests/core/test_registry_export.py` 和 `tests/integration/test_tachibana_m2.py` 要求它和 `sources/*.yaml` 一致）。用 `uv run --frozen rikolto registry export --out catalog` 生成，只提交本任务来源的文件；`catalog/` 下其他文件不应有变化，有变化就说明改到了别的来源，要查清楚。
- `tests/regressions/test_audit_wisteria_102.py` 已在 `feature/0.4` 上改为只固定原有的 7 个来源（提交 42efc70），本任务不需要、也不许改它。

# 重做要求（第 1 轮，2026-10-31）
前两次尝试的 Federal Register 实现和专项测试都通过了，只是全量测试里 3 个测试失败：`catalog/` 少了 `us-fedreg.json`，以及 102 回归测试固定了来源列表。这两处都是任务文件没写清楚，已按上一节补上。本轮从 `feature/0.4` 重新开始，其余要求不变。
