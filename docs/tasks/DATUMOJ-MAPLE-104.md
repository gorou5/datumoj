---
id: DATUMOJ-MAPLE-104
title: 写入租约（条件写入取得、续期、释放，--force 只接管过期租约）、条件发布、到期来源选择
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-001", "DATUMOJ-MAPLE-102"]
runner: codex
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/lease.py", "src/datumoj/core/state.py", "src/datumoj/core/schedule.py", "src/datumoj/cli.py", "tests/core/test_lease.py", "tests/core/test_schedule_due.py", "results/DATUMOJ-MAPLE-104/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/core/test_schedule.py"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_lease.py
    - cmd: make check T=tests/core/test_schedule_due.py
    - cmd: make check T=tests/core/test_schedule.py
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-104/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
同一时间只有一个写入者能改 R2 上的状态：Actions 和本机写入前都要拿到租约，拿不到就不写；发布代次时再校验一次租约，被抢先的这一轮作废。Actions 每次运行能算出该跑哪些来源。

# 背景
开发线：A 云端运行与运行基础。计划 6.1、6.3 节：`concurrency` 只能串行化 Actions 自己的运行，挡不住本机。租约规则见 ADR 0005（001 写出）。102 的推送留了“发布前检查”回调。本机已有定时调度（`core/schedule.py` 的 `Scheduler`：`next_due` 按来源的 `schedule` 和 `runs.jsonl` 的历史算到期，`control.json` 记来源开关，`locks/` 下是来源锁）。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-104/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `lease.py`：按 ADR 0005 用 `StateObjectStore` 的条件写入实现取得、续期、释放、查看。租约有到期时间；持有者在到期前续期；释放只能由持有令牌的一方做。`--force` 只能接管**已过期**的租约，对仍有效的租约一律拒绝（测试覆盖）。时间可注入，测试不 sleep。
2. 推送接上租约：`datumoj state push` 在远端状态已配置时必须持有有效租约，发布前通过 102 的回调再校验一次令牌；租约在推送期间过期或被接管时本轮作废、不切换指针。
3. 到期来源选择：`datumoj schedule due [--data-dir] --json` 输出当前到期且已开启的来源（复用 `next_due` 和现有历史读取，不另写一套频率规则）；Actions 恢复代次后用它决定本轮跑哪些来源。
4. 本机让路：配置了远端状态（`DATUMOJ_S3_BUCKET` 等已设置）时，本机的 `datumoj schedule tick` / `run-now` 先取得租约，拿不到就跳过并在输出和 `runs.jsonl` 里记清楚原因（不报错退出）；没配置远端状态时行为与现在完全一样。
5. CLI：`datumoj state lease acquire|renew|release|status [--force]`。输出 JSON，不打印令牌以外的敏感信息（令牌只给持有者本次进程用，日志里不打印完整令牌）。
6. 测试：两个写入者竞争取得只有一个成功；持有者续期成功、他人续期失败；过期后 `--force` 接管成功、未过期时 `--force` 失败；发布前租约被接管则发布失败、指针不变；没配置远端时 `tests/core/test_schedule.py` 原有测试全部不改且通过。
7. 测试断言：命令输出和日志里不出现测试用的凭证值、endpoint 和 bucket 名。
8. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不写 Actions 工作流（105）。
- 不改来源的 `schedule` 写法。
- 不做按来源把本机调度交给 Actions 的开关（租约已保证单写入者；需要时另排）。
