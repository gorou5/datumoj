---
id: DATUMOJ-TACHIBANA-205
title: 政策网页定向采集器：种子、深度、域名白名单、robots、URL 规范化、链接的 PDF
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-001"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/gov_web.py", "sources/jp-gov-web.yaml", "tests/crawlers/test_gov_web.py", "results/DATUMOJ-TACHIBANA-205/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_gov_web.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-205/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
按种子清单抓取省厅和自治体的政策栏目页，限定深度和域名，遵守 robots.txt，页面链接的 PDF 一起下载并记下它来自哪个页面。

# 背景
开发线：B 文档线。计划第 5 节、6.2 节。采集器只决定抓什么，执行（限速、条件请求、robots、存 raw、写 manifest、上限）由 IVY 的执行器 `core/runner.py` 和 `core/http.py` 负责（`blocked_by_robots` 已有）。样本在 `tests/fixtures/gov_web/`（说明和种子清单见该目录 README）。采集逻辑的测试主要用合成小站点 `html/synthetic/site/`：种子页 `policy/startup/index.html` 的链接覆盖了二级页、带片段和 `utm_*` 的重复链接、robots 禁止的 `private/`、`guide.pdf`、前缀外的 `/policy/other/out.html`、外部主机、`mailto:` / `javascript:` / `tel:`；`detail-a` → `deeper/detail-b` → `deeper/deep/detail-c` 是深度 1 到 3。jGrants 的 `spa/robots.txt`（`Disallow: /` 加 `Allow: /index.html`）可测 Allow 优先。H2 会把种子清单定稿到栏目级；本任务先用 README 里的种子。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-205/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `kind = "gov_web"`。`sources/jp-gov-web.yaml`：`scope.seeds` 是列表，每项 `url`、`label`（例如“大阪市 創業支援”）、`path_prefix`（只跟进该前缀下的链接，默认取种子 URL 的目录）；`scope.max_depth` 默认 2；`scope.max_pages_per_seed` 默认 200；`scope.follow_pdf` 默认 true；种子先放 README 里已核对规约的三个站点：内阁府（`www8.cao.go.jp`，`/cstp/openinnovation/`）、总务省（`www.soumu.go.jp`，`/denshijiti/`）、大阪市（`www.city.osaka.lg.jp`，`/keizaisenryaku/`），`allowed_hosts` 列出这三个主机（东京都、大阪府、经产省是否加入由 H2 定，本任务不加）；`schedule: daily`；许可写 `unverified` / `internal_only`；来源默认关闭（开关在 control.json，不在 yaml）。
2. `plan` 产出种子任务（meta：`role=page`、`seed`、`depth=0`）；`after_fetch` 对成功的 HTML 页解析链接：只跟进同一 `allowed_hosts`、在 `path_prefix` 之下、深度不超过 `max_depth` 的 HTML 链接；链接到 `.pdf`（或 content-type 为 PDF）的，在 `follow_pdf` 时生成 PDF 任务，meta 写 `role=pdf` 和父子关系（契约的 `META_PARENT_URL`、`META_PARENT_SHA256`）。
3. URL 规范化后再去重：主机小写、去掉片段（#…）、去掉默认端口、去掉 `utm_*` 等跟踪参数、解析相对路径和 `../`；同一次运行里同一规范化 URL 只出一次任务。`mailto:`、`javascript:`、`tel:` 和非 http(s) 链接忽略。
4. 每个种子超过 `max_pages_per_seed` 就不再追加该种子的任务，并在报告里可见（例如 meta 或 warning，写清做法）。
5. `classify`：HTML 正文去掉标签后少于 100 字的判为 `spa_shell`（与执行器默认一致即可不覆盖）。
6. 测试用 `pytest-httpserver` 回放合成小站点（主机换成本地服务器，`allowed_hosts` 在测试里覆盖）：`detail-c`（深度 3）不抓、`/policy/other/out.html` 和外部主机不抓、`private/` 不请求、`guide.pdf` 带父子关系、`detail-a` 的三种写法只出一个任务、`mailto:` 等忽略、每种子上限，以及跑两次时第二次走条件请求（沿用执行器）；另用真实种子页（例如 `html/osaka-city/seed.html`）断言能抽出前缀内的详情页链接。

# 不做
- 不执行 JavaScript，不登录，不提交表单。
- 不做全站爬取。
- 不写 HTML 正文解析（202）和 PDF 解析（201）。
