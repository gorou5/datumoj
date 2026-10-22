---
id: DATUMOJ-TACHIBANA-209
title: 政策网页采集器：每种子的网页和 PDF 分开计数
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-208"]
runner: codex
model: null
tier: low
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/gov_web.py", "sources/jp-gov-web.yaml", "tests/crawlers/test_gov_web_pdf_cap.py", "tests/crawlers/test_gov_web.py", "results/DATUMOJ-TACHIBANA-209/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/integration/test_gov_web_e2e.py"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_gov_web_pdf_cap.py
    - cmd: make check T=tests/crawlers
    - cmd: make check T=tests/integration/test_gov_web_e2e.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-209/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
每个种子的网页数和 PDF 数分开封顶，PDF 再多也不会挤掉网页。

# 背景
开发线：B 文档线。m1 真实抓取（2026-10-22，上限 500）：206 修好 `<base href>` 后，大阪市种子下有 228 个网页、271 个 PDF，合计 499 碰到 `max_pages_per_seed`（现在网页和 PDF 共用一个计数，`crawlers/gov_web.py` 的 `_task` 里 `self._counts[seed] += 1`），网页被 PDF 挤掉。2026-10-22 人定：网页和 PDF 分开计数。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-209/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `scope.max_pages_per_seed` 只数网页（种子页和 `role=page` 任务）；新增 `scope.max_pdfs_per_seed`（正整数，校验写法与现有 scope 一致），只数 `role=pdf` 任务；不设时默认等于 `max_pages_per_seed`。
2. 两个计数各自到上限后，只停该类任务，另一类照常；各自到上限时分别记一次日志（网页沿用现有 `max_pages_per_seed=… reached`，PDF 为 `max_pdfs_per_seed=… reached`）。任务 meta 里的计数随之分开：网页沿用 `max_pages_per_seed` / `seed_task_number` / `seed_limit_reached`，PDF 用 `max_pdfs_per_seed` / `seed_pdf_number` / `seed_pdf_limit_reached`（键名可调整，但要写清并测到）。208 跳过的 PDF 不计数。
3. `sources/jp-gov-web.yaml` 加 `scope.max_pdfs_per_seed: 500`（`max_pages_per_seed` 保持 500）；`max_files`（现在 2000，含 304 和失败）按 3 个种子 × (500 + 500) 放宽到 3000。
4. 已有测试里依赖“网页和 PDF 共用计数”的断言（`tests/crawlers/test_gov_web.py` 的 `test_per_seed_cap_is_visible_in_persisted_meta`、`test_budgets_are_independent_and_duplicates_do_not_consume_budget`，以及配置断言）按新语义调整，只改这几处，同一文件的其他测试不改；在 summary 里逐条说明改了什么。
5. 新测试 `tests/crawlers/test_gov_web_pdf_cap.py`：网页到上限后 PDF 仍生成、PDF 到上限后网页仍生成、默认值、非法值报错、日志和 meta。
6. `tests/crawlers` 和 `tests/integration/test_gov_web_e2e.py` 必须仍然通过。

# 不做
- 不改执行器和解析器。
- 不改种子清单。
