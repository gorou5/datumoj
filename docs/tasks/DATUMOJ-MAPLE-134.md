---
id: DATUMOJ-MAPLE-134
title: 清理不再重复下载：去掉整代恢复和逐代 manifest 下载，查询批量化，加时间上限
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-132"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/state.py", "src/datumoj/rikolto/core/manifest.py", "src/datumoj/rikolto/cloud.py", "tests/core/test_state.py", "tests/core/test_state_s3.py", "tests/core/test_manifest.py", "tests/test_cloud_run.py", "tests/test_cloud_embed.py", "tests/regressions/test_audit_122.py", "tests/regressions/test_audit_131.py", "tests/regressions/test_audit_134.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-134/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/runner.py", "src/datumoj/rikolto/core/schedule.py", "src/datumoj/rikolto/core/rawstore.py", "src/datumoj/rikolto/core/rawstore_s3.py", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests/core/test_manifest.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "docs/deploy/runners.md 里的清理耗时说明"]
  review: {by: other_family, checklist: ["清理是否不再下载任何副本块和整代内容", "只看当前代次决定删除 raw 是否仍然安全：每个保留代次恢复都不报缺 raw", "回滚之后标记消失或重新标记时，是否不会提前删除", "副本块是否仍然只在不被保留代次引用时才删", "raw_retention 是否不再逐快照查询，结果和原来相同", "到时间上限时是否安全停下、不影响发布和退出码，下一轮接着清", "结果 JSON 和进度行是否只多了白名单字段", "是否越界"]}
  human: sampled
max_attempts: 5
---
# 目标
清理的耗时不随保留代次数增长，一轮内最多占固定时长，不再把作业拖到 90 分钟被取消。

# 背景
- 2026-11-05 Cloud run 41932584838 超过 90 分钟被取消：第 25 代已发布，但清理没跑完，也没有结果和 issue 同步。
- 发布结束到本轮结束的时间：570、760、662、865 秒，最后一轮超过 1,219 秒。
- 代码里的三处开销（`core/state.py`）：
  1. `cleanup` 开头用 `_materialize(raw="remote")` 完整恢复一次当前代次。
  2. 对每个保留代次调用 `_generation_raw`，串行下载 manifest 并计算 `_raw_refs`。每小时一轮时最多 72 代。
  3. `raw_retention` 对每个快照分别查 `snapshot_meta` 和 `publish_state`；`mark_raw` 和 `cleanup` 都调用它。

依赖 132（都改 `core/state.py`、`cloud.py`）。任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线（假存储、假时钟），不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. **去掉整代恢复**：`cleanup` 不再调用 `_materialize`。它只在发布和发布后校验成功之后运行，不需要再校验一遍。
2. **不再逐代下载 manifest**，删除 `_generation_raw`，raw 的判定只用工作目录里的当前 manifest：
   - 可删除：当前 manifest 的标记超过 72 小时（`now - marked_at >= 72h`）、年龄达到保留期、仍是 `raw_retention` 的候选、不在当前代次的必需引用里。
   - 为什么安全：保留代次的创建时间都在最近 72 小时内，所以都晚于这个标记，也就都带着它，恢复时不算必需；当前代次本身就是判定来源。
   - 回滚：回滚后当前 manifest 没有这个标记，就不删；之后重新标记，时间重新算。
   - 这段推理用一行注释写在代码里。
3. **副本和旧代次**：继续只用代次清单（`generation.stores`）判定，规则不变。
4. **批量查询**：`raw_retention` 用少量 SQL 一次取出所需字段，不逐快照查询，结果必须和原实现相同。需要时在 `core/manifest.py` 加只读方法。
5. **时间上限**：`cleanup` 增加一个时间上限常量（600 秒）。到时间后不再开始新的删除，结果记 `timed_out: true`，不算失败，不改变本轮结果和退出码；下一轮接着清。开始规划前先检查作业剩余时间，不足时直接跳过并同样记录。
6. **可观察**：结果 JSON 的 `cleanup` 增加 `elapsed_s`、`timed_out`；进度行增加 `cleanup_end` 事件。只加这些，其他白名单不变。
7. **测试**：
   - 新增 `tests/regressions/test_audit_134.py`：72 个保留代次时，用计数的假存储断言清理下载 `copies/` 的次数为 0，代次数加倍时读取次数基本不变。
   - 清理后当前代次和每个保留代次都能完整恢复（`raw="remote"`、`raw="local"`）。
   - raw：标记不足 72 小时不删；超过且到期才删；回滚后不删；重新标记后时间重新算；待解析和 staged 不删。
   - `raw_retention` 新旧结果一致（多来源、最新一份保护、`raw_expire_latest_types`、待解析、staged）。
   - 时间上限：到时间后停止删除，下一轮接着清，结果和发布不受影响。
   - 已有测试和断言不变；如果某条直接依赖 `_generation_raw` 或清理里的恢复，只改这一点，并在 `docs/deploy/runners.md` 列一行说明。
8. `docs/deploy/runners.md` 更新：清理怎么判定、时间上限、为什么不用逐代下载、回滚时的行为。
9. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。
10. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，并写进 `docs/deploy/runners.md`。

# 不做
- 不改清理规则（72 小时、保留期、开关）。
- 不改发布后校验（132）和 `parse_pending` 的 HEAD（133）。
- 不操作真实 R2，不改仓库变量，不改工作流。
