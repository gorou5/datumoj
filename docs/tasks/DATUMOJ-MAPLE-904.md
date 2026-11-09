---
id: DATUMOJ-MAPLE-904
title: 检查点 m1 真实运行：加只读的“读取代次”工作流，人触发后记录验收
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-901", "DATUMOJ-MAPLE-117", "DATUMOJ-MAPLE-143"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: [".github/workflows/cloud-pull-check.yml", "scripts/pull_check.py", "tests/test_pull_check.py", "tests/test_workflows*.py", "results/DATUMOJ-MAPLE-904/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/workflows/cloud-run.yml", ".github/workflows/cloud-embed.yml", ".github/workflows/cloud-cleanup.yml", ".github/workflows/ci.yml", "src/**", "sources/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pull_check.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-904/summary.md 完成说明（含给人的触发与核对清单）"]
  review: {by: human, checklist: ["工作流是否只读（不写 R2、不拿租约、不推送）", "日志里是否只有计数、来源 ID、代次号，没有凭证、endpoint、bucket 名和原文", "是否和 cloud-run 用同一并发组", "是否越界"]}
  human: required
max_attempts: 3
---
# 目标
补上目标 3“读取代次”在真实环境的验证手段：一个只能手动触发的工作流，在 Actions 上把 R2 上最新的完整代次 `pull` 到 runner 临时目录，用 MCP 工具的同一套函数各问一次，只输出计数。人触发一次，把结果记进 m1 验收文档，904 就算完成。

# 背景
开发线：集成。计划 `docs/plans/0.3-maple.md` 第 3 节目标 3、第 7 节 904 行：手动和定时运行、代次发布、租约互斥已在真实 R2 上跑过，剩“经 Actions 读取代次”的验证和验收记录。本机不放 R2 密钥（2026-10-30 定），所以读取也在 Actions 上做。

任务分支从 `feature/0.4` 拉出，合并回它。参考 `.github/workflows/cloud-cleanup.yml`（MAPLE-143）的写法：同样的 action 版本锁定、`permissions: contents: read`、只在 `gorou5/datumoj` 上运行、secrets 只经 env 传入。
- **仓库和 Actions 日志都是公开的**：输出只写代次号、来源 ID、计数、耗时和对象键的相对部分，不写凭证、endpoint、bucket 名和任何资料原文（检索命中只输出命中数、来源 ID 和 `cite` 是否还原成功）。
- 依赖已锁定，不新增依赖，不改 `pyproject.toml`。
- 测试全部离线，S3 用 moto；不连真实 R2，不触发真实工作流。
- 不写账号、密钥、本机绝对路径。

# 要求
1. 新增 `.github/workflows/cloud-pull-check.yml`（名称 “Cloud pull check”）：只有 `workflow_dispatch`；并发组与 `cloud-run.yml` 相同（`rikolto-cloud-run`，不取消进行中的）；超时不超过 60 分钟；不写 R2、不拿租约、不推送、不改 issue。
2. 新增 `scripts/pull_check.py`：对给定目录做 `pull`（复用 `cli.py` 里现有的 `pull_state`，不另写拉取逻辑），然后在拉下来的目录上调用 MCP 工具背后的同一套函数：`version`、`coverage`、一条日文 `search_docs`、对第 1 条命中的 `cite`、一条 `query_sql`（例如数观测值表行数）。输出一个 JSON：代次号、拉取字节数和耗时、各来源快照数、检索命中数、`cite` 是否还原、SQL 行数、退出码。任一步失败退出码非 0。
3. 不拉向量也能完成检查时就不拉向量；如果 `pull_state` 必须整代拉取，在 summary 里写明拉取体积。
4. 新增 `tests/test_pull_check.py`（moto、合成来源、假嵌入）：发布一个代次后跑 `pull_check.py`，输出字段齐全、`cite` 还原成功；R2 上没有有效代次时退出码非 0；输出里不含 endpoint、bucket 名、凭证和检索原文。
5. summary 写给人的清单：触发命令、要核对的字段、预期范围，以及写进 `docs/acceptance/v0.3.0-maple-m1.md` 的那几行的格式。
6. `make lint` 0 条，全量测试全部通过、要跑完；全量命令排除现存的 1 个 xfail，不改那个测试，不新增 xfail 或 skip。
7. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改 `cloud.py`、`state.py` 和现有工作流。
- 不建定时触发。
- 不在本机读 R2。
