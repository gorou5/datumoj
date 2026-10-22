---
id: DATUMOJ-TACHIBANA-104
title: 不支持条件请求的来源：先发 HEAD 比对 Last-Modified 和大小，没变就不下载正文
project: DATUMOJ
status: todo
priority: 2
depends_on: ["DATUMOJ-TACHIBANA-103"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/contracts.py", "src/datumoj/testing/**", "src/datumoj/core/http.py", "src/datumoj/core/runner.py", "tests/core/test_head_probe.py", "results/DATUMOJ-TACHIBANA-104/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**"]
protected: ["tests/fixtures/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_head_probe.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-TACHIBANA-104/summary.md 完成说明"]
  review: {by: human, checklist: ["默认参数下行为是否与现在完全一样", "HEAD 判为未变化的条件是否只在 Last-Modified 和大小都相同时成立", "HEAD 是否同样遵守限速、robots、重定向白名单", "契约改动是否只加了两个可选关键字参数", "全部已有测试是否通过", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
服务器不支持条件请求（ETag 每次不同、`If-Modified-Since` 也返回 200）的来源，打开 `scope.head_probe` 后，先发 HEAD 比对 Last-Modified 和大小，都没变就记为未变化、不下载正文。

# 背景
开发线：A 抓取基础。m1 真实抓取（2026-10-22）发现：内阁府（www8.cao.go.jp，Apache）同一文件每次返回的 ETag 不同（`"127d-65460b198782e"` / `"127d-65460b1988368"` 交替），只带 `If-Modified-Since` 也返回 200，每天约 100 个文件（含大 PDF）都整份重下。内容哈希不变，不产生新版本，只是浪费流量。2026-10-22 人定：不支持条件请求的来源先 HEAD 比对，PDF 等附件另按更长周期复查（208）。执行器在 `core/runner.py`（`deps.fetcher.fetch(task.url, etag=…, last_modified=…)`，上一次成功快照来自 `manifest.last_success`，含 `size`、`etag`、`last_modified`），HTTP 客户端在 `core/http.py`（`PoliteHttpFetcher`，限速、robots、重定向白名单）；`HttpFetcher` 协议在 `core/contracts.py`。

计划见 `docs/plans/0.2-tachibana.md`（总计划 `docs/PLAN.md`；与团队规范 `AGENTS.md`、`docs/interoperability.md` 的取舍见 PLAN 第 1.1 节）。本版本在 `feature/0.2` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除此之外，只能 import 本任务 `depends_on` 里列出的、已合并任务的模块，以及 IVY 已有的模块；其他 TACHIBANA 任务的实现模块一律不 import（集成任务 9xx 除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-TACHIBANA-104/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。

# 要求
1. 契约（本任务允许改，向后兼容）：`HttpFetcher.fetch` 增加只能用关键字传的可选参数 `size: int | None = None`、`head_probe: bool = False`。默认值下行为与现在完全一样；`testing/` 的假实现和合格测试同步（假实现接受这两个参数）。不改其他协议和模型。
2. `PoliteHttpFetcher.fetch` 在 `head_probe=True` 且 `last_modified` 和 `size` 都有值时：先发 HEAD（同样走限速、robots、重定向白名单、重试）；HEAD 返回 2xx 且 `Last-Modified` 与传入值相同、`Content-Length` 与 `size` 相同时，直接返回 `not_modified`（带 HEAD 的 etag / last_modified，`http_status` 记 HEAD 的状态），不发 GET；任何一项不同、缺失，或 HEAD 返回 405 / 501 / 其他错误时，照常发条件 GET（与现在一样）。HEAD 的结果不写 raw。
3. 执行器把上一次成功快照的 `size` 传给 `fetch`；`head_probe` 取 `spec.scope.get("head_probe", False)`（必须是布尔值，否则报清楚的错）。`max_files` 计数、运行报告、manifest 记录与现在的 304 情形一致（HEAD 判为未变化时记 `not_modified`）。
4. 限速：HEAD 也占一次请求间隔；HEAD 之后需要 GET 时，GET 再等一个间隔。
5. 新测试 `tests/core/test_head_probe.py`（`pytest-httpserver`）：Last-Modified 和大小都相同 → 只有 HEAD、结果 `not_modified`；Last-Modified 不同、大小不同、缺 Content-Length、HEAD 405 → 都会 GET；`head_probe=False` 时不发 HEAD；HEAD 被 robots 禁止 → 不发请求；HEAD 也遵守限速；执行器从 `scope.head_probe` 读开关并传 `size`。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改 `sources/*.yaml`（打开开关由 208 做）。
- 不做按周期跳过（208）。
