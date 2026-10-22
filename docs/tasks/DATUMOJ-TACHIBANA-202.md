---
id: DATUMOJ-TACHIBANA-202
title: HTML 正文抽取：去掉导航、按 h1–h3 分节切块、日期抽取、空页识别
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-001"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/html_page.py", "tests/parsers/test_html_page.py", "results/DATUMOJ-TACHIBANA-202/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_html_page.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-202/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
省厅和自治体的政策网页能抽出正文、按节切块，引用能定位到节；页面上的更新日能抽出来；空页面和 SPA 外壳不会被当作成功。

# 背景
开发线：B 文档线。计划 6.2 节。样本在 `tests/fixtures/gov_web/html/`（说明见 `tests/fixtures/gov_web/README.md`）：真实页面有内阁府（`cao/`，UTF-8）、总务省（`soumu/`，Shift_JIS，响应头没有 charset）、大阪市（`osaka-city/`）的种子页和详情页，以及 jGrants 的 SPA 外壳（`spa/shell.html`）；东京都、大阪府的规约不允许再分发、经产省返回 403，所以没有它们的页面。合成页 `synthetic/dated-wareki.html` 有导航、面包屑、侧栏、页脚、h1–h3、超过 1500 字的一节，以及 `最終更新日：令和7年4月1日` 和 `掲載日：2025年3月15日`。只用已锁定的 lxml，不加新依赖。编码用 `datumoj.text.decode_bytes`（cp932 页面很多）。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-202/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 解析器声明 `source_kind = "gov_web"`、`content_type = "text/html"`。
2. 去掉 `script`、`style`、`noscript`、`nav`、`header`、`footer`、`aside`、`form`，以及 `role=navigation` 和 id / class 明显是导航的块（例如含 `breadcrumb`、`gnav`、`globalnav`、`sidebar`、`side-menu`、`footer`、`header`、`pagetop`）；优先取 `main` 或 `role=main`，没有时取正文最多的块。规则写成常量表，每个站点样本都要有测试。
3. 按 h1–h3 分节：每节一个或多个 `Chunk`，`locator` 为 `section=<标题路径，用 ` > ` 连接>`；一节超过 1500 字时在段落边界拆开，加 `para=<序号>`。`title_path` 填标题路径。`Document.kind = html_page`，标题取 `h1`，没有取 `<title>`。
4. 日期抽取：从正文和页面元信息里找“更新日”“最終更新日”“掲載日”“公開日”“作成日”等字样后的日期，支持西历（2025年4月1日、2025/4/1、2025-04-01）和和历（令和7年4月1日，用 `datumoj.text` 的和历转换）；取最新的“更新日”类日期作为 `published_at`，抽不到就留空（回答时按“无数据日期”），不能拿抓取日顶替。
5. 正文去空白后少于 100 字的页面（SPA 外壳、空页、只有导航的页）抛 `ParseError`，信息含“empty_content”，这样会记为解析失败而不是成功。
6. `doc_key` 为 `<source_id>:page:<规范化 URL 的 sha1 前 16 位>`；快照 meta 里的父子关系（`parent_ref`）、`seed` 标签原样写进 `Document.metadata`。
7. 测试覆盖每条：内阁府、总务省、大阪市各至少一个样本，断言正文里有该页的关键句、没有导航文字、节标题路径正确、页面有日期时日期正确；`dated-wareki.html` 断言和历转换后 `published_at` 为 2025-04-01（取更新日而不是掲載日）、长节按段落拆开；`spa/shell.html` 断言 empty_content。

# 不做
- 不执行 JavaScript，不抓链接（链接由采集器 205 负责）。
- 不改采集器和接线。
