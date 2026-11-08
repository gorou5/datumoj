---
id: DATUMOJ-MAPLE-142
title: 云端轮次记录异常类型，向量维护失败不拖垮发布
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/cloud.py", "scripts/source_issues.py", "tests/test_cloud_run.py", "tests/test_source_issues.py", "tests/regressions/test_audit_142.py", "results/DATUMOJ-MAPLE-142/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_source_issues.py
    - cmd: make check T=tests/regressions/test_audit_142.py
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd"
  evidence: ["测试输出日志", "变更文件清单"]
  review: {by: other_family, checklist: ["输出里是否只有异常类名，没有异常消息、路径或密钥", "向量维护失败后是否仍然发布并执行清理", "租约检查失败是否仍然让整轮失败，且 phase 不再记成 maintenance", "generation 为 None 时是否不关闭也不新开任何 issue"]}
  human: sampled
max_attempts: 5
---
# 目标
云端轮次失败时能从日志看出异常类型；向量维护失败不再让已跑完的来源白跑。

# 背景
- 2026-11-06 的一轮定时报 `maintenance_failed`：569 条未发布，清理也没执行。`cloud.py` 的 `except Exception` 只记 `reason`，日志里没有异常类型，查不出原因。下一轮同样的步骤成功了。
- `reason` 里的 `maintenance` 同时覆盖 `maintain_vectors` 和紧随其后的 `check()`，分不清是哪个失败。
- `core/state.py` 的 `_copy_boundary` 已经容忍向量维护失败（记警告后继续），发布时还会再维护一次，所以 `cloud.py` 这一处没必要让整轮失败。
- 整轮失败时 `generation` 是 `None`，`scripts/source_issues.py` 的 `issue_text` 抛 `invalid_count`，同步步骤也跟着报错。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. `_round` 的整轮 `except Exception` 把异常类名写入 `result["error_type"]`（只用 `type(exc).__name__`）。不写异常消息、堆栈或路径，它们可能带密钥。没有异常时该字段为 `None`，云端轮次和 embed_only 的结果都有这个字段。
2. `cloud.py` 里的 `maintain_vectors` 失败不让整轮失败：捕获后在 `result["maintenance_error"]` 记异常类名（平时为 `None`），继续发布和清理。写法参照同一段里 `mark_raw` 的处理。
3. `maintain_vectors` 之后的 `check()` 改记到 `lease_check` 阶段。租约丢失仍然让整轮失败，不发布。
4. `scripts/source_issues.py`：`result["generation"]` 是 `None` 时直接返回 0，不调用 `gh`，不关闭也不新开 issue。原因是这轮没有发布。其他 `generation` 非法值仍按现有校验报错。
5. `tests/test_cloud_run.py::test_whole_round_failure_preserves_current_and_releases` 的 `maintenance` 参数与要求 2 冲突：从该参数列表移除，并新增测试说明新行为。其余参数和断言不改。
6. 新增 `tests/regressions/test_audit_142.py`，覆盖：
   - 向量维护失败时本轮仍发布新代次并执行清理，`exit_status` 为 0，`maintenance_error` 是类名；
   - 整轮异常时 `error_type` 是类名，输出里没有异常消息和任何环境变量值；
   - 维护之后租约检查失败时不发布，`reason` 是 `lease_check_failed`；
   - `generation` 为 `None` 时 `sync_issues` 返回 0，且没有调用 `gh`。
7. `make lint` 0 条，全量测试全部通过，要跑完。全量命令排除 `test_wisteria_m1.py` 里现存的 1 个 xfail，不要修改那个测试，也不要新增 xfail 或 skip。
8. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，写进交付说明。

# 不做
- 不改 `core/state.py`、`index/vector.py` 和工作流。
- 不查也不修 `maintain_vectors` 本身失败的原因，先有日志再说。
- 不操作真实 R2 和 GitHub。
