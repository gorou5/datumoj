---
id: DATUMOJ-MAPLE-139
title: 抓取中止判 failed、失败比例按文件算，Federal Register 永久失败不再卡住新文档
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/crawlers/fedreg.py", "docs/deploy/runners.md", "tests/test_cloud_run.py", "tests/test_cloud_reports.py", "tests/test_source_issues.py", "tests/crawlers/test_fedreg.py", "tests/integration/test_fedreg_e2e.py", "tests/regressions/test_audit_136.py", "tests/regressions/test_audit_138.py", "tests/regressions/test_audit_139.py", "results/DATUMOJ-MAPLE-139/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_source_issues.py
    - cmd: make check T=tests/crawlers/test_fedreg.py
    - cmd: make check T=tests/regressions/test_audit_139.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单", "docs/deploy/runners.md 里更新后的判坏规则"]
  review: {by: other_family, checklist: ["plan_error、after_fetch_error 是否一定判 failed，且旧失败抵扣不会把它们抵消", "http_error、network_error 这类单项失败是否没有被误判为抓取中止", "失败比例是否按文件计算，抓取和解析没有重复计入分母", "Federal Register 永久失败时新文档是否能入库，覆盖进度是否绝不越过未抓到的文档", "回放不读 XML 正文后，缺失的 XML 快照是否仍被当作未完成"]}
  human: sampled
max_attempts: 5
---
# 目标
接口变化造成的抓取中止一定会开 issue；个别文档永久失败不会让 Federal Register 再也抓不到新文档。

# 背景
- 任务 138 的规则把 `failed` 限定为异常逃到 `_source` 的 `except Exception`。但 `plan` 和 `after_fetch` 的异常被 `core/runner.py` 捕获，记成 `plan_error` / `after_fetch_error`，到不了那里。
- 2026-11-05 之前 Federal Register 的真实情况：列表页成功，`after_fetch` 抛异常，每轮 `new=1, parsed=1, crawl_failed=1`，没有抓到一篇文档。按现在的公式 `successes=2, attempts=3`，`1*2 > 3` 不成立，判 `degraded`：退出码 0、算成功运行、已有 issue 被关闭。接口真的变了也发现不了。
- 现在的分母 `new + unchanged + parsed + failures` 把新文件的抓取和解析各算一次，新文件失败要超过约 2/3 才判 failed。
- 任务 136 之后，`FedregCrawler.plan` 在 `resume is not None` 时只产出未完成任务就 `return`。窗口里只要有一条详情或 XML 永久失败（404、robots），之后每轮只补这一条，不再搜索新窗口。这是相对旧行为的退化，审查和 CodeRabbit 都指出了。
- `_progress` 和 `_pending` 回放时会读所有已抓内容，包括 XML 正文，而 `_children` 对 `role=xml` 根本不用正文。云端每次读取都是一次 R2 请求，这段时间计入单来源 900 秒时限。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. `report.failures` 里有 `plan_error` 或 `after_fetch_error` 时，来源直接判 `failed`（等同 `crashed`），不管有多少成功项，也不被旧失败抵扣。用明确的名单，不能用 `_error` 后缀判断：`http_error`、`network_error` 是单项失败，规则不变。
2. 失败比例按文件计算，满足任一项就判 `failed`：
   - 抓取：本轮首次抓取失败数 > (new + unchanged + 抓取失败数) 的一半；
   - 解析：本轮首次解析失败数 > (parsed + 解析失败数) 的一半。
   恰好一半仍是 `degraded`。“成功数为 0”的规则和旧失败不计入比例的规则不变。
3. Federal Register：未完成窗口的任务继续重试，同时每轮也要发现新文档，不能因为有永久失败就不再搜索。覆盖进度（`covered`）绝不能越过未抓到的文档。已成功的文档不重复请求详情和 XML。`tests/integration/test_fedreg_e2e.py` 现有的 `block_xml` 断言应当继续成立；确实要改时只改必要部分，并写进 `docs/deploy/runners.md`。
4. Federal Register：回放时不读 `role=xml` 快照的正文，只看它的 outcome 和 sha。同一次 `plan` 里 `_progress` 和 `_pending` 共用已读内容，同一个 sha 最多读一次。新增测试统计 `raw.get` 次数。
5. 更新 `docs/deploy/runners.md` 的判坏规则。
6. 新增 `tests/regressions/test_audit_139.py`，覆盖：
   - 按上面背景里的 Federal Register 真实数字（`new=1, parsed=1, after_fetch_error=1`）判 failed、退出码 1、连续两轮后开 issue；
   - `plan_error` 判 failed；
   - 3 个新 URL 中 2 个死链判 failed，1 个死链判 degraded；
   - `jp-egov-data` 的情况（119 个新增、50 个 `http_error`）判 degraded；
   - 一条详情永久 404 时，多轮之后新发布的文档能入库，覆盖进度不越过失败的文档；
   - 重放时 XML 正文不读取，同一 sha 只读一次。
   fixtures 不改，测试数据写在测试里。
7. 已有测试断言了旧公式的，只改这一点，并在 `docs/deploy/runners.md` 列一行说明。
8. `make lint` 0 条，全量测试全部通过，要跑完，不要中途中断。全量命令排除 `test_wisteria_m1.py` 里现存的 1 个 xfail（调度器把 xfail 算作失败，与本任务无关）。不要修改那个测试，也不要新增 xfail 或 skip。
9. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进 `docs/deploy/runners.md`。

# 不做
- 不改 `core/runner.py` 和其他抓取器。
- 不改 `sources/*.yaml`、工作流和 issue 阈值。
- 不操作真实 R2 和 GitHub。
