---
id: DATUMOJ-TACHIBANA-208
title: 政策网页采集器：PDF 按更长周期复查；jp-gov-web 打开 HEAD 比对
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-104", "DATUMOJ-TACHIBANA-206"]
runner: codex
model: null
tier: low
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/gov_web.py", "sources/jp-gov-web.yaml", "tests/crawlers/test_gov_web_recheck.py", "tests/crawlers/test_gov_web.py", "tests/integration/test_gov_web_e2e.py", "results/DATUMOJ-TACHIBANA-208/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_gov_web_recheck.py
    - cmd: make check T=tests/crawlers
    - cmd: make check T=tests/integration/test_gov_web_e2e.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-208/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
政策网页每天复查，链接的 PDF 按来源配置的周期（jp-gov-web 为 7 天）复查；内阁府这类不支持条件请求的站点用 HEAD 比对（104）。

# 背景
开发线：B 文档线。2026-10-22 人定：PDF 等附件很少原地修改，按更长周期复查，周期按来源设置；网页照常每天。采集器在 `crawlers/gov_web.py`：`after_fetch` 从页面链接生成 `role=pdf` 任务，已能用 `ctx.manifest.last_success(url)` 查上一次成功快照（含 `fetched_at`）。104 已让执行器按 `scope.head_probe` 先发 HEAD 比对。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-208/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. `scope.pdf_recheck_days`：可选，正整数（类型不对报清楚的错，与现有 scope 校验写法一致）；不设时行为与现在一样（每次都生成 PDF 任务）。设了时，`after_fetch` 遇到 PDF 链接：上一次成功快照的 `fetched_at` 距本次运行开始不足这么多天就不生成任务；超过、或从没成功过就照常生成。跳过的 PDF 仍算“已见”（同一次运行里不重复判断），不计入每种子页数上限；跳过的数量写进日志（例如 `gov_web pdf_recheck skipped=N`）。
2. 时间一律取可注入的时钟（沿用 `ctx` 里已有的写法），测试不依赖真实时钟。
3. `sources/jp-gov-web.yaml`：`scope.pdf_recheck_days: 7`，`scope.head_probe: true`。`tests/crawlers/test_gov_web.py` 里检查配置的那条断言随之调整（只改这一处）。
4. 新测试 `tests/crawlers/test_gov_web_recheck.py`：6 天前成功的 PDF 不生成任务、8 天前的生成、从没成功过的生成、不设 `pdf_recheck_days` 时都生成、非法值报错；网页任务不受影响。
5. `tests/integration/test_gov_web_e2e.py::test_conditional_rerun_downloads_no_bodies_or_document_versions` 读的是真实的 `sources/jp-gov-web.yaml`，原来断言第二次运行时 PDF 也被条件请求（`新增 0，未变化 3`）；设了 `pdf_recheck_days: 7` 后 PDF 在 7 天内不再请求，这是 2026-10-22 人定的新行为。只改这一个测试，改为断言新行为：网页仍走条件请求（未变化 2）、PDF 没有被请求、文档版本不变；同一文件的其他测试一律不改。（第 1、2 次尝试因该文件被设为受保护而卡在这里，其余验收都通过。）
6. `tests/crawlers` 和 `tests/integration/test_gov_web_e2e.py` 必须仍然通过。

# 不做
- 不改执行器和 HTTP 客户端（104 已做）。
- 不改种子清单和上限。
