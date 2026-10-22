---
id: DATUMOJ-TACHIBANA-207
title: HTML 日期抽取：认大阪市页面日期块 page_day01（m1 真实抓取发现）
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-202"]
runner: claude
model: null
tier: low
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/parsers/html_page.py", "src/datumoj/wiring.py", "tests/parsers/test_html_page_dates.py", "tests/parsers/test_html_page.py", "results/DATUMOJ-TACHIBANA-207/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/integration/test_gov_web_e2e.py"]
acceptance:
  automated:
    - cmd: make check T=tests/parsers/test_html_page_dates.py
    - cmd: make check T=tests/parsers
    - cmd: make check T=tests/integration/test_gov_web_e2e.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-207/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
大阪市页面的页面日期能抽成 `published_at`，回答时能带上数据日期，而不是“无数据日期”。

# 背景
开发线：B 文档线。m1 人工验收的真实抓取（2026-10-22）发现：208 个政策网页没有一个抽出日期。内阁府和总务省的页面本身没有写更新日，抽不到是对的；大阪市页面把日期写在 `<div class="page_day01"><p>2026年8月26日</p></div>` 里，前面没有“更新日”等字样，`parsers/html_page.py` 的 `_published_at` 只认带字样的日期和 meta，所以漏掉。样本 `tests/fixtures/gov_web/html/osaka-city/detail-1.html`（2026年8月26日）和 `detail-2.html`（2026年10月16日，m1 真实抓取时录入）都带这个块。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-207/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 在 `_published_at` 里加一张“页面日期块”常量表：按 class / id 认定的元素（先放大阪市的 `page_day01`），元素文字整体是一个日期（西历或和历，沿用 `_parse_date`）时作为更新日类候选；元素里还有其他文字、或不是日期时忽略。只认表里的块，不从正文里随便取日期（正文里的截止日、阁议决定日等不能当页面日期）。
2. 与现有规则合并：仍取所有更新日类候选里最新的一个；没有候选仍留空，不拿抓取日顶替。
3. 提高解析器版本号，使 `datumoj parse` / `index` 会重新解析已有的大阪市快照、补上日期（不需要重新抓取）。注意：实际入库走的是 `wiring.py` 里的分派入口 `_GovWebContent`（name `gov-web-content`，按正文选 PDF / HTML 解析器），解析记录按它的版本记；只改 `HtmlPageParser.version` 不会触发重新解析。所以要让 `_GovWebContent.version` 随内部两个解析器的版本变化（例如由两者的版本拼成），并加测试断言 HTML 解析器版本变化会让分派入口的版本跟着变。在 summary 里说明版本提升对已入库数据的影响。
4. 新测试 `tests/parsers/test_html_page_dates.py`：`osaka-city/detail-1.html` 的 `published_at` 为 2026-08-26，`detail-2.html` 为 2026-10-16；`page_day01` 里不是日期、带其他文字、表外 class 的同样结构都不取；内阁府、总务省样本的日期结果不变；`dated-wareki.html` 仍为 2025-04-01。
5. 202 留下的测试 `tests/parsers/test_html_page.py::test_osaka_unlabeled_page_date_is_not_treated_as_published_date` 断言大阪市样本的 `published_at is None`，与本任务（2026-10-22 人定）相反：只把这一个测试改为断言 2026-08-26（可改名），`test_html_page.py` 里的其他测试一律不改。（第 1、2 次尝试因任务文件把该文件设为受保护而卡在这里，实现本身其余测试都通过。）
6. `tests/parsers` 和 `tests/integration/test_gov_web_e2e.py` 必须仍然通过。

# 不做
- 不改采集器。
- 不做站点专用的其他抽取规则（只加日期块表）。
