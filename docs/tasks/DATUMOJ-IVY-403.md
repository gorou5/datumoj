---
id: DATUMOJ-IVY-403
title: 修复：CKAN 增量只以完整走完的全量为基准，上次没走完就退回全量
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/crawlers/ckan.py", "tests/crawlers/test_ckan_baseline.py", "results/DATUMOJ-IVY-403/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/crawlers/test_ckan.py"]
acceptance:
  automated:
    - cmd: make check T=tests/crawlers/test_ckan_baseline.py
    - cmd: make check T=tests/crawlers/test_ckan.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-403/summary.md 完成说明"]
  review: {by: other_family, checklist: ["只有分页走到末尾的全量才能作为增量基准", "被上限、--limit 或失败打断的全量是否会让下次退回全量", "退回全量的原因是否写进任务 meta 并能在报告里看到", "原有 401 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
CKAN 采集器的增量抓取只以**分页完整走到末尾**的那次全量抓取为基准；如果最近一次全量没有走完（被上限、`--limit` 或失败打断），下一次自动退回全量，而不是只看“最近变化”列表。

# 背景
开发线：D 目录线（修复 DATUMOJ-IVY-401）。M2 真实抓取验收（H4）时：第一次全量抓取只拿到第 1 页目录就被执行器的 bug 停掉了（已由 106 修复）。修好后再跑，`_load_previous` 把那次没走完的全量的 `run_started` 当作增量基准，只请求了 `recently_changed_packages_activity_list`，看到没有变化就结束了——200 个数据集的资源一个都没下载。

现在的基准取法（`src/datumoj/crawlers/ckan.py` 的 `_load_previous`）：任何 `role` 为 `package_search` 或 `package_show` 的成功快照，都拿它的 `run_started` 当基准。

计划见 `docs/PLAN.md`；测试全部离线（`datumoj.testing.fakes.drive_crawler` 加 `tests/fixtures/ckan_egov/` 的两页样本和空页），不能改依赖、契约和执行器。

# 要求
1. 一次全量“完整”的判定：同一个 `run_started` 下，`package_search` 一直翻到了末页（返回条数为 0 或少于 `rows`）。判定所需的信息可以在 `_load_previous` 里从 manifest 和 raw 读出（快照的 meta 与正文都在），也可以在 `after_fetch` 处理末页时另外产出一个能被识别的标记——方式自定，写进 summary。
2. 增量基准只取**最近一次完整全量**的 `run_started`。最近一次全量没有完整走完时（无论是 `--limit`、`max_files`、`max_total_bytes` 还是某页失败），本次退回全量，`meta["incremental_fallback"]` 写明原因（例如“上次全量未完成”）。
3. 以完整全量为基准的增量运行之后，下一次增量的基准顺延为这次增量的开始时间（保持原有行为）；增量运行本身不能把“未完成的全量”变成完整。
4. 原有 `tests/crawlers/test_ckan.py` 已列为 protected，必须全部通过。
5. 新测试 `tests/crawlers/test_ckan_baseline.py`：
   - 全量完整走完 → 下一次走增量；
   - 全量在第 1 页后被 `max_files` 或 `limit` 打断 → 下一次退回全量，fallback 原因正确；
   - 全量某页失败 → 下一次退回全量；
   - 完整全量之后的增量，再下一次仍是增量。

# 不做
- 不改执行器、HTTP 客户端、单文件上限配置、契约。
