---
id: DATUMOJ-MAPLE-138
title: 来源判坏规则：个别文件失败不再把整个来源判坏，真坏了才开 issue
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/pipeline.py", "scripts/source_issues.py", "docs/deploy/runners.md", "tests/test_cloud_run.py", "tests/test_cloud_reports.py", "tests/test_cloud_embed.py", "tests/integration/test_gov_web_e2e.py", "tests/test_source_issues.py", "tests/test_pipeline.py", "tests/test_pipeline_retry.py", "tests/regressions/test_audit_130.py", "tests/regressions/test_audit_138.py", "results/DATUMOJ-MAPLE-138/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_source_issues.py
    - cmd: make check T=tests/test_pipeline_retry.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "docs/deploy/runners.md 里的判坏规则说明"]
  review: {by: other_family, checklist: ["抓取器抛异常、成功数为 0、失败过半是否一定判 failed", "degraded 的失败数量是否仍留在结果里，没有被吞掉", "连续失败计数和 issue 开关是否一致，degraded 不会造成 issue 反复开关", "解析超时重试是否不会让一轮的时间失控", "deadline 的现有语义是否不变"]}
  human: sampled
max_attempts: 5
---
# 目标
`source-broken` issue 只在来源真的坏了时出现。个别死链或坏文件留在结果里，但不让整个来源一直显示失败。

# 背景
2026-11-06 核对第 28 代的 manifest 和 runs.jsonl：
- `jp-egov-data` 累计 1,403 个成功，上一轮 119 个新增、50 个失败，失败主要是目录里的死链（403、404、robots、空响应），这些数据本身就拿不到。
- `jp-jma` 只有 1 个文件的失败，连续 12 轮被判失败：它曾经 `budget_exceeded:duration_seconds`，现在重解析只要 0.1 秒。`pipeline.py` 约第 184 行把 `budget_exceeded:` 的结果缓存下来，不重试，却每轮重新计入 `summary.failed`。
- `jp-gov-web` 3 个页面正文不足 100 字（`empty_content`），每轮都算失败。
- 现在任何一项失败都会让 `_source` 记 `status: failed`、退出码 1（`cloud.py` 约第 226–229 行），Actions 每轮都显示失败，真正的问题被淹没。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. 新规则，`failed`（开 issue、退出码 1、计入连续失败）只在以下情况成立：
   - 抓取器或解析阶段抛出未捕获的异常（现有 `except Exception` 分支）；
   - 本轮有失败，且本轮抓取成功数（new + unchanged）和解析成功数都是 0；
   - 单项失败超过一半：本轮新失败数 > 本轮总尝试数的 50%。
2. 其他有单项失败的情况判 `degraded`：
   - 退出码 0，不开 issue，不计入连续失败。已有的 `source-broken` issue 按恢复处理并关闭。
   - `failures` 和 `categories` 的数量照常保留在结果 JSON 里，`source_health` 的 status 为 `degraded`。
   - `last_successful_runs` 里算成功运行。
3. “本轮新失败”只算这一轮第一次失败的项。已经失败过、这轮重试还失败的项不进入第 1 条的判断，但仍然记录在结果里，并按现有方式重试。
4. 解析超时可以重试：`budget_exceeded:duration_seconds` 不再永久缓存，之后的轮次重试。其他 `budget_exceeded:*`（页数、字符数、块数）是确定性的，保持现状，但不重复计入失败。
5. `empty_content` 不算失败：不计入 `failures` 和判坏，不让 Actions 失败。可以留一个单独的计数。
6. `deadline` 语义不变：只因时间用完而没有其他失败的，和现在一样不算失败。
7. 更新 `scripts/source_issues.py` 和 `docs/deploy/runners.md`，写明三种状态和判定规则。
8. 新增 `tests/regressions/test_audit_138.py`，覆盖：
   - 死链占少数时判 degraded、退出码 0、不开 issue；
   - 抛异常、成功为 0、失败过半时判 failed；
   - 正好 50% 判 degraded；
   - 旧失败重试仍失败不算新失败；第 1 次解析超时、下一轮成功后恢复；
   - `empty_content` 不算失败；
   - 已有 issue 的来源变成 degraded 后 issue 关闭，不反复开关；
   - 只因 deadline 停止的语义不变。
9. 已有测试断言了旧规则的（比如任何失败都是 failed），只改这一点，并在 `docs/deploy/runners.md` 列一行说明。
10. `make lint` 0 条，全量测试全部通过，要跑完，不要中途中断。全量命令排除 `test_wisteria_m1.py` 里现存的 1 个 xfail（调度器把 xfail 算作失败，与本任务无关）。不要修改那个测试，也不要新增 xfail 或 skip。
11. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进 `docs/deploy/runners.md`。

# 不做
- 不改解析器和抓取器（任务 136、137 负责）。
- 不改工作流，不改重试频率以外的调度逻辑。
- 不操作真实 R2 和 GitHub。
