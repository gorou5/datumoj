---
id: DATUMOJ-TACHIBANA-901
title: 检查点 m1 集成：政策网页 + PDF 端到端
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-201", "DATUMOJ-TACHIBANA-202", "DATUMOJ-TACHIBANA-205"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/wiring.py", "src/datumoj/cli.py", "sources/jp-gov-web.yaml", "tests/integration/test_gov_web_e2e.py", "results/DATUMOJ-TACHIBANA-901/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_gov_web_e2e.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-901/summary.md 完成说明"]
  review: {by: human, checklist: ["是否只改了接线和 sources", "节和页的引用是否正确", "失败是否没被当成成功", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
政策网页和它链接的 PDF 用真实实现跑通：回放样本 → 抓取 → 解析 → 索引 → 检索，引用能定位到节和页，PDF 能说出来自哪个页面。

# 背景
开发线：集成。TACHIBANA 检查点 m1 的集成门槛（计划第 7 节）。只允许改接线和 `sources/`。人工验收时会在这台机器上对种子清单做一次真实抓取（H 步骤），结果记到 `docs/acceptance/v0.2.0-tachibana-m1.md`。

本任务合并并通过人工验收后，打 tag `v0.2.0-tachibana-m1`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-901/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `sources/jp-gov-web.yaml` 能被注册表加载，`kind` 与采集器对上；新来源默认关闭。
2. 端到端测试：`pytest-httpserver` 回放 `tests/fixtures/gov_web/` → crawl → parse → index；`search_docs` 能检索到政策网页的节（`section=`）和 PDF 的页（`page=`），出处带时间口径；PDF 的文档 metadata 带父页面 URL。
3. SPA 外壳和空页面记为失败（抓取或解析），不计入成功；robots 禁止的路径没有被抓；超出深度和白名单的链接没有被抓。
4. 重复运行第二次不重复下载（条件请求），不产生新的文档版本。
5. （固定要求）集成测试**不能写入仓库里受 git 跟踪的路径**（包括 `results/`）：评测报告等证据默认写到临时目录，只有设置了环境变量 `DATUMOJ_EVIDENCE_DIR` 时才写到该目录（参考 `tests/integration/test_law_e2e.py` 的写法）。
6. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改各开发线的模块。
