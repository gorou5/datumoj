---
id: DATUMOJ-MAPLE-110
title: 解析与抓取预算（D-12）：页数、累计文本、输出块、时长上限，抓取总时限
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-107", "DATUMOJ-MAPLE-108", "DATUMOJ-MAPLE-111", "DATUMOJ-MAPLE-203"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/budget.py", "src/datumoj/parsers/pdf.py", "src/datumoj/parsers/html_page.py", "src/datumoj/pipeline.py", "src/datumoj/core/runner.py", "tests/core/test_budget.py", "results/DATUMOJ-MAPLE-110/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_budget.py
    - cmd: make check T=tests/parsers
    - cmd: make check T=tests/core/test_runner.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-110/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
一个异常大的文件（几千页的 PDF、几十 MB 的文本）不会让解析无限占用时间和内存；一次抓取运行有总时限。超限时记一条清楚的失败，不发布半截结果。

# 背景
开发线：A 云端运行与运行基础。审核 D-12：现在只有下载字节上限（`core/http.py`）。PDF 解析（`parsers/pdf.py`）逐页读文本存进列表，没有页数、累计文本、时长限制；管线（`pipeline.py`）在本进程里直接调用解析器；抓取（`core/runner.py`）没有整次运行的时限。失败分类用已有的 `FailureKind.policy`（与 `too_large` 同类）。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核问题的回归用例在 `tests/test_evidence_regressions.py`（MAPLE-002 写出，修好之前标 `xfail(strict=True)`，原因里写着负责的任务号）。修好本任务负责的用例后**删掉它们的 xfail 标记**，不改断言；其他任务的用例不动。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-110/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `core/budget.py`：`ParseBudget`（最大页数、最大累计文本字符数、最大输出块数、最长时长；默认值写成模块常量并在 summary 里说明取值依据）和一个可协作检查的计时器 / 计数器；可由来源配置 `scope.parse_budget` 覆盖。
2. PDF 和 HTML 解析器在循环里协作检查预算（每页 / 每节检查一次），超限抛 `BudgetExceeded`（带哪一项超限）；管线在解析返回后再检查输出块数和文本量。
3. 超限的快照记为解析失败，错误文本以 `budget_exceeded:<项>` 开头，分类为 `policy`、不可重试（同一解析器版本下不反复重试）；不写 ChunkStore、不发布。
4. 抓取：`run_crawl` 加整次运行的时限（来源配置 `scope.max_run_seconds`，默认值写成常量），到时停止派发新任务，`stop_reason` 记 `deadline`，已完成的快照照常保留。
5. 新测试 `tests/core/test_budget.py` 覆盖每一项预算的超限和刚好不超限、来源配置覆盖、失败分类、不发布、抓取时限（时间可注入，不依赖真实等待）。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不把解析放进子进程或沙箱。
- 不改下载字节上限。
