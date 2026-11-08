---
id: DATUMOJ-MAPLE-135
title: 清理改成批量删除，每批检查一次租约，不再逐个删除
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/state.py", "src/datumoj/rikolto/core/state_s3.py", "tests/core/test_state.py", "tests/core/test_state_s3.py", "tests/test_cloud_run.py", "tests/regressions/test_audit_131.py", "tests/regressions/test_audit_134.py", "tests/regressions/test_audit_135.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-135/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_state_s3.py
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T="tests --deselect=tests/integration/test_wisteria_m1.py::test_us_dollar_series_ranks_first_for_gdp_usd --deselect=tests/integration/test_wisteria_m1.py::test_eurostat_series_identity_does_not_split_by_period"
  evidence: ["测试输出日志", "变更文件清单", "docs/deploy/runners.md 里的批量删除说明"]
  review: {by: other_family, checklist: ["删除范围是否仍限于代次清单、copies、raw，协调对象绝不能进批次", "每批之前是否检查租约和时间上限", "部分失败时计数是否准确、是否停止后续删除", "到时间上限或租约丢失时是否安全停下，不影响发布和退出码", "代次清单删除失败时是否不再删内容对象", "没有批量方法的存储是否仍能逐个删除", "是否越界"]}
  human: sampled
max_attempts: 5
---
# 目标
清理速度不再被逐个删除限制，一轮能删完几千个到期对象。

# 背景
- 2026-11-05 Cloud run 42003273693：删 732 个对象用了 549 秒，每个约 0.75 秒。
- 现在是串行删除，每删一个都检查一次租约，每轮最多约 800 个。
- 已有 7,213 个 raw 标记，最早 2026-11-08 10:20（UTC）之后的一轮开始到期。按现在的速度至少要 9 轮以上才能删完。
- `delete_if_match` 的 R2 条件删除本来就不作为协调手段（代码注释已写明）；安全边界是租约，加上这些命名空间里的对象内容不可变。

任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线（假存储、假客户端），不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. 给 `S3StateObjectStore` 加批量删除，用 S3 DeleteObjects，每批最多 200 个键。`contracts.py` 和测试夹具不改；其他存储没有这个方法时，退回现有的逐个删除。
2. `state_s3.py` 加批量版的 `delete_retired_objects`：
   - 沿用 `delete_retired_object` 的键校验：只允许代次清单、`copies/sha256/`、`raw/`，协调对象绝不能进批次。
   - 只删清单里列出的键。
   - 每批之前检查一次租约和时间上限。
3. `cleanup` 的删除顺序不变：代次清单、副本块、raw。代次清单删除失败时，仍然不再删内容对象。
4. 部分失败：逐项读响应里的 `Errors`，只把真正删掉的计入 `*_deleted` 和 `*_deleted_bytes`，失败计入 `failures` 并停止后续删除，下一轮重新规划。不改变发布结果和退出码。
5. 规则不变：72 小时、保留期、开关、600 秒上限、结果 JSON 字段都不动。
6. 测试全部离线：
   - 新增 `tests/regressions/test_audit_135.py`：用计数的假 S3 客户端，1,000 个对象的调用次数不超过 5 次批量删除，租约检查次数等于批次数。
   - 批内出现 current.json、write-lease.json 或其他前缀时，整批都不删并报错。
   - 部分失败时计数准确；租约丢失时不再开始下一批；到时间上限时停止，下一轮接着删。
   - 清理后当前代次和保留代次都能完整恢复。
   - 已有测试和断言不变；直接依赖逐个删除的只改这一点，并在 `docs/deploy/runners.md` 列一行说明。
7. `docs/deploy/runners.md` 更新：批量删除、每批的租约检查、部分失败的处理。
8. `make lint` 0 条，全量测试全部通过，全量测试要跑完，不要中途中断。
   全量命令排除 `tests/integration/test_wisteria_m1.py` 里 2 个已知 xfail（WISTERIA-902 带入，与本任务无关，调度器会把 xfail 算作失败）。不要修改这两个测试，也不要新增 xfail 或 skip。命令见 acceptance。
9. 调度器里没有人能回答问题，不要等待回复。遇到范围缺口，在 scope 内选最小的做法完成，并写进 `docs/deploy/runners.md`。

# 不做
- 不改清理规则和保留期，不并发删除。
- 不操作真实 R2，不改仓库变量，不改工作流。
