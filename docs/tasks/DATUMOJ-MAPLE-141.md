---
id: DATUMOJ-MAPLE-141
title: JMA 只抓保留期内的条目，不再回补 7 天窗口
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/crawlers/jma_feed.py", "docs/deploy/runners.md", "tests/crawlers/test_jma_feed.py", "tests/regressions/test_audit_141.py", "tests/integration/test_stats_e2e.py", "results/DATUMOJ-MAPLE-141/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_jma_feed.py
    - cmd: make check T=tests/regressions/test_audit_141.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "docs/deploy/runners.md 里的 JMA 窗口说明"]
  review: {by: other_family, checklist: ["截止点是否取 feed 自身的 updated，而不是本机时钟", "恰好在截止点上的条目是否保留，时间缺失或无法解析的条目是否不被丢弃", "未配置 raw_retention_days 时行为是否完全不变", "feed 无变化时的提前返回和已保存条目的跳过是否不受影响", "--limit 是否只计入真正排入抓取的条目"]}
  human: sampled
max_attempts: 5
---
# 目标
JMA 不再回补保留期外的条目，避免 R2 超过团队的 9 GB 上限。

# 背景
- 实测 `regular_l.xml` 的 7 天窗口有 9,461 条，每天新增约 1,350 条；每个文件在 R2 占约 0.55 MB（raw 0.36 MB，其余是状态副本）。
- `sources/jp-jma.yaml` 设了 `scope.raw_retention_days: 3`。现在的抓取器把窗口里没保存过的条目全部排入，而 raw 3 天后就删，回补更早的条目只会多占空间。
- `JmaFeedCrawler.after_fetch` 在 `saved_ids` 之外的条目上循环排任务；`_feed_entries` 目前只返回 `(entry_id, url)`。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. `scope.raw_retention_days` 是正数时，跳过 `updated` 早于截止点的 entry。截止点 = feed 自身的 `updated` − `raw_retention_days` 天。不用本机时钟，保证回放结果确定。
2. 恰好在截止点上的条目保留。entry 的 `updated` 缺失或无法解析时不跳过，照常抓取。feed 的 `updated` 无法解析时不做截止，照常抓取。比较时要正确处理时区。
3. 没有配置 `raw_retention_days` 或取值不是正数时，行为完全不变。
4. 被跳过的条目不计入 `ctx.limit`，不写失败，不产生快照。
5. `previous_updated == updated` 时的提前返回要改成只看保留期内的条目：保留期外的未保存条目不能让这个判断失效。
6. 在 `docs/deploy/runners.md` 写一小段：JMA 只抓保留期内的条目及原因，并说明窗口更早的条目不会回补。
7. 新增 `tests/regressions/test_audit_141.py`，覆盖：
   - 窗口里 2 条在保留期内、2 条在保留期外，只排入前 2 条；
   - 恰好在截止点上的条目保留；
   - entry 的 `updated` 缺失时照常抓取；feed 的 `updated` 无法解析时不截止；
   - 不配 `raw_retention_days` 时全部排入；
   - `--limit` 只计真正排入的条目；
   - feed 无变化且只剩保留期外的未保存条目时，不再排入任何条目。
   fixtures 不改，测试数据写在测试里。
8. `tests/integration/test_stats_e2e.py::test_jma_second_feed_downloads_only_new_projected_entries` 用的 `tests/fixtures/jma/regular_l.snapshot*.xml` 里有保留期外的条目，和要求 1 冲突，前 4 次尝试都败在这里。这个测试验的是“第二次 feed 只下载新条目”，与保留期无关。在这个测试文件里用最小改动让它不受保留期影响：只给测试构造的 `jp-jma` 来源去掉 `scope.raw_retention_days`，或在路由层改写内存里的 `<updated>`。原有断言一条都不能删、不能放宽；不改 fixtures；`sources/jp-jma.yaml` 不动。
9. `make lint` 0 条，全量测试全部通过，要跑完，不要中途中断。全量命令排除 `test_wisteria_m1.py` 里现存的 1 个 xfail（调度器把 xfail 算作失败，与本任务无关）。不要修改那个测试，也不要新增 xfail 或 skip。
10. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进 `docs/deploy/runners.md`。

# 不做
- 不改 `sources/*.yaml`、工作流、`cloud.py` 和 `core/`。
- 不改 raw 保留和清理逻辑。
- 不操作真实 R2 和 GitHub。
