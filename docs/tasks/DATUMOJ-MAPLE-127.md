---
id: DATUMOJ-MAPLE-127
title: cloud run 到期来源排序：从没跑过的先跑、按上次开始时间从旧到新，被截止待续跑的排最后
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/cloud.py", "tests/test_cloud_run.py", "tests/regressions/test_audit_127.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-127/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/**", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/pipeline.py", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-127/summary.md 完成说明"]
  review: {by: other_family, checklist: ["从没跑过的来源是否排在最前", "常规到期的来源是否按上次开始时间从旧到新", "因 deadline 补进到期列表的来源是否排在最后", "到期集合本身（哪些来源到期）是否不变，只改顺序", "同条件下顺序是否确定（按 source_id 打破平局）", "结果 JSON 的 due 计数和已有字段是否不变", "是否改了 core 或越界"]}
  human: sampled
max_attempts: 3
---
# 目标
每轮 Cloud run 都让更多来源轮到。一个被截止、等着续跑的大来源，不能每轮都排在最前、把整轮预算吃光。

# 背景
2026-11-02 到 11-03 的第 3、4 轮 Cloud run：
- 第 4 轮有 7 个来源到期，但只跑了 `jp-egov-law` 一个：从第 41 秒跑到第 2470 秒，被整轮预算截止。
- 原因：`cloud._due()` 把上次因 `deadline` 停下的来源补进到期集合，然后 `sorted()` **按 source_id 字母序**返回。`jp-egov-law` 每轮都被截止，每轮都重新到期；字母序又靠前，于是每轮都先跑它，把 40 分钟预算用完。
- 122 的审查报告当时就提过这个风险（"due 按字母序，可能长期挤占排在后面的来源"）。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 只改 `cloud.py` 的排序。`core/schedule.py` 的到期判断不改，哪些来源到期也不变。
- 测试全部离线，沿用 `tests/test_cloud_run.py` 的 `world` 夹具和假时钟。不新增依赖。仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. `_due()` 返回的列表按以下顺序排列：
   1. **从没跑过**的来源（账本里没有它的尝试记录）；
   2. **常规到期**的来源（`scheduler.due()` 给出的），按上次尝试的开始时间从旧到新；
   3. **只因上次 `deadline` 才补进来**的来源，同样按上次开始时间从旧到新。
   同一档内开始时间相同时，按 `source_id` 排序，保证结果确定。
   一个来源既常规到期、上次又是 deadline 时，算第 2 档。
2. 读上次尝试的开始时间，沿用 `scheduler.status()` 已经给出的 `last_run`。不另读账本，不改 core。
3. 结果 JSON 的 `due` 计数和其他字段不变。
4. **测试**：
   - `tests/test_cloud_run.py`：
     - 三档来源混在一起时，返回顺序正确；
     - 假时钟下，第一轮某来源被截止，第二轮其他到期来源先跑，被截止的来源排在最后；
     - 到期集合与改前相同（只是顺序变了）。
   - 新增 `tests/regressions/test_audit_127.py`：复现"字母序靠前的大来源每轮被截止"的场景，断言连续两轮里，排在后面的到期来源至少跑到一次。
   - 已有的测试和断言全部不变。如果有断言依赖旧的字母序，在 summary 里列出来并说明为什么要改；只改顺序断言，不改其他内容。
5. `docs/deploy/runners.md` 补一两句话，说明来源的执行顺序。
6. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不改 `core/schedule.py`，不改到期规则，不改各来源的抓取上限。
- 不改嵌入（另有任务）。
