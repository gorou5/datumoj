---
id: DATUMOJ-MAPLE-308
title: describe 按表代次缓存，query_sql 整个请求一个预算（D-10）
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-307"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/store/observations.py", "src/datumoj/analysis/tools.py", "tests/store/test_query_budget.py", "results/DATUMOJ-MAPLE-308/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/store/test_query_budget.py
    - cmd: make check T=tests/store
    - cmd: make check T=tests/analysis
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-308/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
`query_sql` 不再每次都扫一遍全部统计表做 `describe`；超时覆盖整个工具请求（连接、查询、出处、元数据），一个慢请求不会拖住后面的工具调用太久。

# 背景
开发线：C 统计线。审核 D-10：`query_readonly`（`store/observations.py`）在 `_connect()` 之后才开始计时；`AnalysisTools.query_sql`（`analysis/tools.py`）每次都调用 `describe`，`describe` 对所有 Parquet 做分组、distinct、计数和时间范围扫描，不受同一超时约束。MCP 只有一个工作线程（`mcp_server.py`）。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-308/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `describe` 结果按“表代次”缓存：代次由各 Parquet 文件的路径、大小、修改时间算出；表有写入（`write_table`）或文件变化时失效。缓存在进程内，测试可断言第二次不再扫描。
2. `AnalysisTools.query_sql` 在请求入口用可注入的单调时钟创建一个绝对截止时间（默认预算沿用现有 `timeout_s`）；将同一 deadline 传入 `query_readonly` 的 `_connect`、SQL 执行、出处回查，以及工具层 `_describe_view` / `describe`（包括缓存未命中扫描及结果组装）。各阶段只使用剩余时间，不得重新获得完整 timeout；连接前即启动预算检查，并为阻塞操作安排可取消 / 可中断的执行边界，不能仅在慢操作返回后检查。超时返回明确的 `QueryTimeout` 错误，不返回部分查询结果或半截出处；清理计时器和连接，超时任务不得干扰后续请求。保持已有调用签名兼容，额外 deadline 通过实现类可选参数或内部适配方法传递，不要求修改受保护的 ObservationStore 协议。
3. 新测试 `tests/store/test_query_budget.py` 覆盖：缓存命中与失效；分别注入慢连接、慢查询、慢出处回查及慢 describe，验证四种操作均受同一绝对截止时间约束（包括前面阶段已消耗部分预算的情形，不能分阶段重置），超时不返回部分出处，每种超时后后续请求均正常。采用注入时钟和可控阻塞 / 中断替身，不依赖真实长等待。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改 MCP 的线程模型。
