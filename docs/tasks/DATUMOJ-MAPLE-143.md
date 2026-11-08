---
id: DATUMOJ-MAPLE-143
title: 云端只清理模式：不抓取、不发布，先删过期数据
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/cli.py", ".github/workflows/cloud-cleanup.yml", "tests/test_cloud_cleanup_only.py", "tests/test_cloud_workflow.py", "tests/regressions/test_audit_143.py", "results/DATUMOJ-MAPLE-143/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/workflows/cloud-run.yml", ".github/workflows/cloud-embed.yml", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_cleanup_only.py
    - cmd: make check T=tests/regressions/test_audit_143.py
    - cmd: make check T=tests/test_cloud_workflow.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单"]
  review: {by: other_family, checklist: ["只清理模式是否绝不抓取、不发布、不写代次，也不新增标记", "存在未发布孤儿代次时是否拒绝清理", "预演模式是否绝不删除任何对象", "默认是否预演，且新工作流是否只能手动触发、没有定时", "输出和日志里是否没有异常消息、路径或密钥"]}
  human: sampled
max_attempts: 5
---
# 目标
新增只清理模式，让 R2 能先腾出空间再写入。

# 背景
- R2 全账户 8.879 GB。一轮是先写入、发布，再清理，下一轮峰值约 9.2 GB，会越过 9 GB 硬停线（AGENTS.md）。
- `StateManager.cleanup` 已有完整的删除计划、租约检查和 `enabled` 开关；`enabled=False` 时只报告 `reclaimable_*`，不删除。这个函数不用改。
- `Cloud run` 工作流目前停用，停用时手动触发也不行，而且它带定时。所以用单独的工作流。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. `cloud.py` 新增 `cleanup_only(*, work_dir=None, max_minutes: float, delete: bool = False) -> dict`。流程沿用 `_round` 的写法：校验预算和工作目录、探测条件写入、取租约、`restore(root, raw="remote")`、取得来源清单、调 `manager.cleanup`、释放租约。能复用的抽出来复用，`run` 和 `embed` 的行为不变。
2. 只清理模式绝不抓取、解析、嵌入，不调 `push`、`mark_raw`、`publish_previous_week_report`，不产生新代次。除 `write-lease.json` 和删除外，不向存储写任何对象。
3. `delete=False`（默认）是预演：以 `enabled=False` 调 `cleanup`，只报告可回收的数量和字节数。`delete=True` 时才删除；删除代次和副本遵守 `R2_CLEANUP`，删除 raw 遵守 `R2_RAW_CLEANUP`，与现有语义一致。
4. 存在未发布的孤儿代次（`generations/<当前+1>.json` 已存在）时不清理，返回 `status="failed"`、`reason="orphan_generation"`，不做恢复发布。
5. 结果字段：`status`、`exit_status`、`reason`、`phase`、`mode`（`dry_run` 或 `delete`）、`generation`（当前代次号）、`cleanup`、`restore_timings`。`cleanup.failures > 0` 或 `timed_out` 时 `exit_status` 为 1，`reason` 用固定值。租约被占用时按 `_round` 的方式返回 `skipped`。异常只记固定 `reason` 和 `phase`，不写异常消息、堆栈或路径。
6. `deadline` 要留够 `cleanup` 的完整窗口，不能因 `restore` 耗时而直接 `timed_out` 跳过。
7. `cli.py` 新增 `rikolto cloud cleanup --max-minutes <分钟> [--delete]`，不带 `--delete` 就是预演，输出 JSON，退出码取 `exit_status`。写法照 `cloud embed`。
8. 新增 `.github/workflows/cloud-cleanup.yml`：
   - 只有 `workflow_dispatch`，输入 `delete`（boolean，默认 false），不能有 `schedule`；
   - `concurrency.group` 用 `rikolto-cloud-run`，`cancel-in-progress: false`；
   - 保留 fork 保护 `if: github.repository == 'gorou5/datumoj'`，`permissions` 只有 `contents: read`；
   - action 的固定版本和 `uv` 版本照 `cloud-embed.yml`，不装 Ollama；
   - `delete` 经 `env` 传入 shell 再判断，不直接拼进命令。
9. 新增 `tests/test_cloud_cleanup_only.py` 和 `tests/regressions/test_audit_143.py`，覆盖：
   - 预演不删除任何对象，且报告的可回收量与实际一致；
   - `delete=True` 删除满足条件的 raw，当前代次仍能完整恢复；
   - 未满 72 小时或未到期的 raw 不删除；
   - 全程没有新代次、`current.json` 不变，crawl、push、mark_raw 都没被调用；
   - 孤儿代次时拒绝，什么都不删；
   - 租约被占用时 `skipped`；租约丢失时停止删除；
   - 输出里没有异常消息和环境变量值；
   - 工作流结构：无定时、并发组正确、默认预演。
10. `make lint` 0 条，全量测试全部通过，要跑完。全量命令排除 `test_wisteria_m1.py` 里现存的 1 个 xfail，不要修改那个测试，也不要新增 xfail 或 skip。
11. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改 `core/state.py` 的清理规则和 72 小时标记规则，不提前删除。
- 不恢复或修改 `Cloud run` 工作流和它的定时。
- 不操作真实 R2 和 GitHub。
