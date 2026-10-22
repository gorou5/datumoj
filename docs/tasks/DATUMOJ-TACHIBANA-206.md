---
id: DATUMOJ-TACHIBANA-206
title: 政策网页采集器：按 <base href> 解析相对链接（m1 真实抓取发现）
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-205"]
runner: codex
model: null
tier: low
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/gov_web.py", "tests/crawlers/test_gov_web_base.py", "results/DATUMOJ-TACHIBANA-206/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/crawlers/test_gov_web.py", "tests/integration/test_gov_web_e2e.py"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_gov_web_base.py
    - cmd: make check T=tests/crawlers
    - cmd: make check T=tests/integration/test_gov_web_e2e.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-206/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
页面用 `<base href>` 指定相对链接基准时，采集器按它解析链接，大阪市页面链接的 PDF 能抓到。

# 背景
开发线：B 文档线。m1 人工验收的真实抓取（2026-10-22）发现：大阪市页面带 `<base href="https://www.city.osaka.lg.jp/keizaisenryaku/" />`，PDF 链接写成 `./cmsfiles/contents/…`；`crawlers/gov_web.py` 的 `after_fetch` 只用页面自身 URL 作基准，拼成了 `/keizaisenryaku/page/cmsfiles/…`，15 个 PDF 全部 404，大阪市的 PDF 一个都没进库。样本 `tests/fixtures/gov_web/html/osaka-city/detail-2.html`（m1 真实抓取时录入）带这个 `<base>`，并有多个 `./cmsfiles/…pdf` 链接；`seed.html`、`detail-1.html` 也带同样的 `<base>`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-206/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 解析链接前先取页面的第一个 `<base href>`：它本身按页面 URL 解析（允许相对写法），解析结果是 http(s) 时用作链接基准；没有 `<base>`、`href` 为空或解析结果不是 http(s) 时，仍用页面 URL（现有行为）。
2. 基准只影响链接解析；域名白名单、`path_prefix`、深度、robots、规范化去重、每种子上限、父子关系等规则不变（白名单外的 `<base>` 不会让白名单外的链接被抓）。
3. 新测试 `tests/crawlers/test_gov_web_base.py`：用 `osaka-city/detail-2.html` 回放（页面 URL 用 README 里的 `…/keizaisenryaku/page/0000681796.html`），断言 `./cmsfiles/…pdf` 解析为 `https://www.city.osaka.lg.jp/keizaisenryaku/cmsfiles/…pdf` 并生成 `role=pdf` 任务；另用临时构造的页面覆盖相对 `<base>`、空 `<base>`、`javascript:` 等非 http(s) 的 `<base>`、指向白名单外主机的 `<base>`。
4. `tests/crawlers` 和 `tests/integration/test_gov_web_e2e.py` 必须仍然通过。

# 不做
- 不改种子清单和 `sources/jp-gov-web.yaml`。
- 不改解析器。
