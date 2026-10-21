---
id: DATUMOJ-IVY-106
title: 修复：单个文件超过单文件上限时只记失败、继续抓取，不再停掉整次采集
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/runner.py", "tests/core/test_runner_file_cap.py", "results/DATUMOJ-IVY-106/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/core/test_runner.py"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_runner_file_cap.py
    - cmd: make check T=tests/core/test_runner.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-106/summary.md 完成说明"]
  review: {by: other_family, checklist: ["采集器 classify 判出的 too_large 是否只记失败、不停止", "累计超过 max_total_bytes、或单个响应在抓取器层被截断时是否仍然停止", "after_fetch 是否在每种情况下都被调用", "原有 105 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
采集执行器区分两种“过大”：来源的**总容量**用尽时停止整次采集；某一个文件超过**单文件上限**时，只把这个文件记为 `too_large` 失败，然后继续抓后面的任务。

# 背景
开发线：A 抓取基础（修复 DATUMOJ-IVY-105）。M2 真实抓取验收（H4）抓 e-Gov 目录（CKAN）时，第 2 个请求是一个超过 50MB 单文件上限的 PDF 资源，CKAN 采集器的 `classify` 正确地把它判为 `too_large`（这是 401 的设计），但 `src/datumoj/core/runner.py` 约第 206 行把**任何** `too_large` 都当成“来源达到总容量上限”，报告 `因上限停止：max_total_bytes=2147483648` 并停止，结果只抓到 1 页目录。

105 审查时确立过一条要求（保留不变）：单个响应在抓取器层就超过来源总容量（`PoliteHttpFetcher.from_spec` 把 `max_bytes` 设为 `spec.max_total_bytes`，超过时中止下载、返回 `too_large`），或累计正文超过 `max_total_bytes` 时，要停止并报告；停止前这一次的 `after_fetch` 照常调用。

单文件上限的具体数值（目前 50MB）由人另行决定，本任务不改配置。计划见 `docs/PLAN.md`；测试全部离线，不能改依赖和契约。

# 要求
1. 停止条件只有两种：累计正文字节超过 `spec.max_total_bytes`；抓取器返回的结果本身就是 `too_large`（在 `classify` **之前**判断）。满足时照旧记录、调用 `after_fetch`、报告“因上限停止：max_total_bytes=…”。
2. `crawler.classify` 返回 `too_large`（采集器自己的单文件上限）时：记录这个快照为 `too_large` 失败（不写 raw），调用 `after_fetch`，**继续**处理后续任务；这个文件的字节不计入累计容量（因为不保存）。报告的失败分类里计 `too_large`，停止原因为空。
3. `max_files` 的停止规则不变。
4. 原有 `tests/core/test_runner.py` 已列为 protected，必须全部通过。
5. 新测试 `tests/core/test_runner_file_cap.py`：用假采集器（`classify` 对超过某大小的正文返回 too_large）和回放：中间一个文件超单文件上限 → 它记为失败、后面的文件照常抓到、没有停止原因；累计超总容量 → 停止；抓取器层 too_large → 停止；每种情况 `after_fetch` 都被调用。

# 不做
- 不改单文件上限的配置、CKAN 采集器、HTTP 客户端、契约。
