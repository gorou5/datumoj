---
id: DATUMOJ-IVY-105
title: 采集执行器：计划→抓取→落库→记录，带上限
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-IVY-101", "DATUMOJ-IVY-102", "DATUMOJ-IVY-103", "DATUMOJ-IVY-104"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/runner.py", "tests/core/test_runner.py", "results/DATUMOJ-IVY-105/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: /opt/homebrew/bin/uv run --frozen pytest -q tests/core/test_runner.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-105/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
任何 `Crawler` 插件都能用同一个执行器跑起来：限速抓取，内容存进 raw，结果写进 manifest，失败和超上限都有记录。

# 背景
开发线：A 抓取基础。B、C、D 三条线的采集器只实现 `Crawler.plan` 等方法，执行全靠这里。这是 A 线交给其他线的成果。

计划见 `docs/PLAN.md`，任务总表、开发线和跨任务约定见 `docs/TASKS.md`。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议和插件发现在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。不要 import 其他开发线的实现模块（集成任务除外）。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-IVY-105/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py）时不要自己改，写进 summary 并停下来问。

# 要求
1. `run_crawl(spec, crawler, deps, limit=None, dry_run=False) -> CrawlReport`。
2. 按注册表的 `rate_per_sec`、`max_files`、`max_total_bytes` 执行；超上限就停下并在报告里写明“因上限停止”。
3. 每次抓取：从 manifest 取上次的 etag/last_modified 做条件请求；成功写 raw；所有结果写 manifest。
4. 默认的 SPA 外壳和空正文判断（插件可以覆盖）：HTML 去标签后少于 N 字，且含 `<div id="root">`、`__next` 等特征。
5. `plan` 可以是生成器，支持边抓边产出新任务（分页）；`after_fetch` 可以追加任务。
6. `CrawlReport`：新增、未变化、失败（按原因计数）、停止原因、用时。`dry_run` 只列计划。
7. 测试用真实的 101–104 实现和一个假采集器加 `ReplayTransport`：正常、304、失败落库、上限停止、dry_run、SPA 外壳。

# 不做
- 不改 scope 以外的文件。
