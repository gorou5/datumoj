---
id: DATUMOJ-MAPLE-117
title: 监控补漏：NAS 写入者身份，缺口提醒不依赖 Actions，pull 显示最后成功时间
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-116"]
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/models.py", "src/datumoj/wiring.py", "src/datumoj/cloud.py", "src/datumoj/cli.py", "src/datumoj/core/monitor.py", "src/datumoj/core/pull.py", "src/datumoj/core/lease.py", "schemas/StateGeneration.schema.json", "deploy/nas/compose.example.yaml", "deploy/nas/env.example", "docs/deploy/nas.md", "tests/core/test_monitor_cloud.py", "tests/core/test_pull.py", "tests/core/test_models.py", "tests/core/test_lease.py", "tests/integration/test_maple_m1.py", "tests/test_cloud_run.py", "tests/test_cloud_reports.py", "tests/regressions/test_audit_117.py", "results/DATUMOJ-MAPLE-117/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example", "docs/PLAN.md", "docs/plans/**", "docs/adr/**", "src/datumoj/core/state_s3.py", "src/datumoj/core/storage.py"]
protected: ["tests/fixtures/**", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/regressions/test_audit_117.py
    - cmd: make check T=tests/core/test_monitor_cloud.py
    - cmd: make check T=tests/core/test_pull.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-117/summary.md 完成说明"]
  review: {by: other_family, checklist: ["NAS 写入的代次是否标为 nas", "旧代次（actions / local）是否仍能读取", "NAS 停机、来源连续失败是否都有提醒", "提醒是否只依赖代次记录、不依赖 Actions", "pull 是否显示每个来源的最后成功时间", "过期的本地副本是否不被误报为云端停滞", "契约改动是否只有 StateWriter.kind", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
写入端换成 NAS 以后，“预期时间内没有成功运行”的提醒照样有效：NAS 运行失败、断电或停机造成的缺口都能被发现，判断只依据状态代次里的记录，不依赖 GitHub Actions。`datumoj pull` 直接告诉人每个来源最后一次成功运行的时间。

# 背景
开发线：A 云端运行与运行基础。2026-10-25 决定：0.3 的写入端从 GitHub Actions 改为 NAS（MAPLE-115、116）。

已有的部分（`core/monitor.py`）：
- `evaluate_cloud_alerts` 按代次里的 `last_successful_runs` 和代次的 `created_at` 计算两种提醒：`cloud_run_missing`（某个来源超过一个调度周期没有成功）和 `cloud_stalled`（代次超过 2 小时没有更新）。它本身不读 Actions 的运行记录。
- `attach_cloud_status` 从本地副本的 `state-pull.json` 读代次信息，给 `datumoj status` 加上这些提醒。
- 周报（`cloud.py` 的 `publish_previous_week_report`）也用同一套计算。

缺口：
- **写入者身份**：`wiring.build_state` 只按有没有 `GITHUB_RUN_ID` 把写入者标成 `actions` 或 `local`；`cloud.py` 的租约持有者固定写成 `actions/<id>`。NAS 上的运行会被标成 `local`，和 Mac 上手动 `state push` 分不出来。`attach_cloud_status` 也只认 `actions` 和 `local`。
- **误报**：`status` 用最后一次 pull 时记下的 `created_at` 计算停滞。如果人 3 小时没 pull，即使 NAS 一直正常，也会显示 `cloud_stalled`。
- **pull 的输出**：`datumoj pull` 只返回代次号和校验结果；最后成功时间只写进了 `state-pull.json`，人看不到。

计划见 `docs/plans/0.3-maple.md`（第 6.3–6.5 节），总计划 `docs/PLAN.md`。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。除要求 1 明确允许的契约补充外，不改契约；需要时写进 summary 并停下来问。
- 回归测试写在 `tests/regressions/test_audit_117.py`，断言修好以后的正确行为；调度器不接受 `skip` / `xfail`。
- 测试全部离线，临时文件一律放 `tmp_path`，不写仓库里受 git 跟踪的路径。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。
- `tests/fixtures/` 下已有的样本只读。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、账号 ID、真实主机名或内网地址、本机绝对路径。

# 要求
1. **契约补充，本任务允许**：`StateWriter.kind` 增加 `"nas"`，变成 `Literal["actions", "local", "nas"]`。已有的 `actions`、`local` 代次照常读取。不改 `StateWriter` 的其他字段。
2. **写入者身份**：写入者类型由环境变量 `DATUMOJ_WRITER_KIND` 指定（只接受 `actions`、`local`、`nas`，其他值拒绝）；没设时保持现在的规则（有 `GITHUB_RUN_ID` 为 `actions`，否则 `local`）。`cloud run` 的租约持有者前缀与写入者类型一致（如 `nas/<id>`），不再固定写 `actions/`。`deploy/nas/env.example` 和 `docs/deploy/nas.md` 写上 `DATUMOJ_WRITER_KIND=nas`。
3. **提醒只依赖代次记录**：`cloud_run_missing` 和 `cloud_stalled` 的输入只有代次里的 `last_successful_runs`、`created_at`、来源的调度频率和当前时间，以及本地运行记录（`_ledger_*`，仅作补充）；不读取 Actions 的任何信息。`attach_cloud_status` 认得 `nas`。用测试确认以下 NAS 场景都会提醒：
   - NAS 停机或断电：远端最新代次的 `created_at` 超过 2 小时 → `cloud_stalled`；
   - NAS 在跑，但某个来源连续失败：代次在更新，该来源的最后成功时间超过一个调度周期 → 该来源 `cloud_run_missing`，其他来源不提醒；
   - 恢复后第一次成功运行：对应提醒消失。
4. **区分本地副本过期和云端停滞**：`status` 只看本地，分不清“云端停了”和“本地太久没 pull”，所以：
   - 本地副本距上次 pull 不超过 2 小时：`status` 照常按上次 pull 时的远端代次计算 `cloud_stalled` 和 `cloud_run_missing`，并写明依据（`cloud.pulled_at`，以及“以上次 pull 时的远端状态为准”）。
   - 本地副本距上次 pull 超过 2 小时：`status` 不报 `cloud_stalled`，只报一条 `replica_stale`（级别低于 `cloud_stalled`，提示先 pull）。
   - 云端是否停滞，由 `datumoj pull` 在拉取时对照远端最新代次判断（要求 5）；pull 之后本地副本是新的，`status` 也会照第一条规则显示 `cloud_stalled`。
   - `state-pull.json` 记下 pull 的时间。
5. **`datumoj pull` 的输出**：在现有返回值里加 `cloud` 一块：代次号、`created_at`、写入者类型、每个来源最后一次成功运行的时间，以及按 pull 当时的时间计算的上述提醒。只输出来源 ID、时间和固定的提醒代码，不输出凭证、endpoint、bucket。
6. 已有的告警规则（包括 JMA 的缺口提醒）不改变行为；周报沿用同一套计算，写入者为 `nas` 时照常生成。
7. **回归测试** `tests/regressions/test_audit_117.py`：覆盖要求 2 的写入者类型和租约前缀（含非法值被拒绝）、要求 3 的三个场景、要求 4 的“本地 3 小时没 pull 时 `status` 只报 `replica_stale`、不报 `cloud_stalled`”和“远端 2 小时没有新代次时，pull 之后报 `cloud_stalled`”、要求 5 的 pull 输出字段。
8. `make check T=tests` 全部通过；与新规则冲突的已有断言允许调整，在 summary 里逐条说明原因，不删测试。
9. **前两次尝试失败的原因，这次要一并改到**（相关文件已加入 scope）：
   - `schemas/StateGeneration.schema.json` 是从模型导出的，`StateWriter.kind` 加了 `nas` 后要用 `export_schemas('schemas')` 重新导出并提交，否则 `test_contracts_state`、`test_contracts_tachibana`、`test_models` 的“已提交 schema 与模型一致”检查失败。不要手改 JSON，也不改这些检查。
   - `core/lease.py` 的 `_holder` 只接受 `local/`、`actions/` 前缀，`nas/<id>` 被拒，`cloud run` 因此 `lease_acquire_failed`。持有者前缀的白名单与 `StateWriter.kind` 保持一致（加 `nas`），`tests/core/test_lease.py` 补对应用例，其他校验（必须有 `/` 和 id、不含控制字符）不变。
   - `tests/integration/test_maple_m1.py` 第 318–325 行：两小时没有新一轮后直接 `schedule status` 并断言 `cloud_stalled`，与要求 4 冲突。改为先 `pull`，再断言 pull 的输出和随后的 `status` 里有 `cloud_stalled`；同时补一条断言：不 pull、时间再过 2 小时后 `status` 只有 `replica_stale`。该测试其余断言不变。

# 不做
- 不做主动推送（邮件、IM 等）通知；提醒仍通过 `status`、`pull` 和周报查看。
- 不改条件写入探测、镜像和 compose 的其他部分（MAPLE-116）。
- 不改 S3 配置读取（MAPLE-115）。
- 不改调度频率和 `cloud_stalled` 的 2 小时阈值。
