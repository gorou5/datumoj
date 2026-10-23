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
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-110/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `core/budget.py`：`ParseBudget`（最大页数、最大单页 / 单节文本字符数、最大累计文本字符数、最大输出块数、最长时长；默认值写成模块常量并在 summary 里说明取值依据）和一个可协作检查的计时器 / 计数器；可由来源配置 `scope.parse_budget` 覆盖。
2. PDF 和 HTML 解析器在提取过程中协作检查预算，并为单页 / 单节设置硬文本边界：PDF 按字符数预检并有界分段提取，HTML 增量解码并流式处理文本事件，累计前检查剩余额度；不能先读取、保存整页文本或构建完整 DOM 再检查预算，单个巨大文本节点也必须受限。超限立即抛 `BudgetExceeded`，明确预算项、上限及已用量，不得被通用解析异常包装丢失类别；管线仍在解析返回后检查输出块数和文本量。
3. 超限的快照记为解析失败，持久化后的错误文本必须以 `budget_exceeded:<项>` 开头（不能加 `BudgetExceeded:` 等异常类名前缀），失败记录及变化日志统一分类为 `policy`；不写 ChunkStore、不发布。`parse_pending()` 在调用解析器前检查已有预算失败：同一 `(sha256, parser, version)` 已有此类失败即跳过，即使快照或上下文指纹不同也不反复解析；解析记录本身仍按四元组键保存，重开 manifest 后该跳过规则仍成立。其他失败保持现有重试流程，解析器版本变化后可重新尝试。
4. 抓取：`run_crawl()` 采用**软截止**（来源配置 `scope.max_run_seconds`，默认值写成常量），从运行开始使用注入的单调时钟计算绝对截止时间；`clock() >= deadline` 即到期。计划生成（含 `next(plan)`）前后、队列取任务前后、限速等待前后及 fetch 前检查，到期停止派发，`stop_reason` 记 `deadline`；限速等待最多等待剩余时间，醒来须复查。已经开始的同步计划生成、`deps.fetcher.fetch()`、回调或持久化不能强制取消，因此运行可能超过时限。计划生成跨过截止时间时丢弃未派发任务，不 fetch、不记快照；截止前开始的 fetch 即使截止后返回，仍按现有校验与容量规则完成 raw / 快照落库和计数，再停止，不执行其追加任务。已完成的快照照常保留；回调迭代也须在取下一项前后检查截止时间。
5. 新测试 `tests/core/test_budget.py` 覆盖每一项预算的超限和刚好不超限、来源配置覆盖、单个 PDF 页面 / HTML 节及巨大文本节点超限时的有界提取、管线输出二次检查。断言落库错误前缀、policy 分类和不发布；连续运行两次（第二次重开 manifest），同一三元组只解析一次，另测不同指纹仍跳过、版本变化可重试、其他失败仍重试。用注入时钟分别覆盖慢 `next(plan)`、队列任务到期、限速等待跨截止、fetch 前恰好到期及 fetch 中跨截止，断言派发次数、等待上限、停止原因与快照是否落库，不依赖真实等待。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不把解析放进子进程或沙箱。
- 不改下载字节上限。
