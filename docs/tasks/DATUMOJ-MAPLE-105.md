---
id: DATUMOJ-MAPLE-105
title: 云端运行：datumoj cloud run（租约 → 恢复 → 跑到期来源 → 维护 → 发布 → 释放）和 GitHub Actions 工作流
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-102", "DATUMOJ-MAPLE-103", "DATUMOJ-MAPLE-104"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/cloud.py", "src/datumoj/cli.py", "src/datumoj/core/schedule.py", "src/datumoj/core/state.py", ".github/workflows/cloud-run.yml", "tests/test_cloud_run.py", "tests/test_cloud_workflow.py", "tests/core/test_state.py", "results/DATUMOJ-MAPLE-105/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".env.example", ".github/workflows/ci.yml"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_cloud_workflow.py
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-105/summary.md 完成说明"]
  review: {by: human, checklist: ["失败时是否不发布、租约是否总被释放", "工作流是否只在 schedule / workflow_dispatch 触发、权限最小", "日志是否可能泄露凭证、endpoint、bucket 或数据", "第三方 action 是否固定版本", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
GitHub Actions 按小时定时（也可手动）跑一轮：取得租约，从 R2 恢复最新代次，只跑到期的来源，解析、建索引和向量，发布新代次，释放租约；失败时不留下半截状态，日志里没有凭证和数据。

# 背景
开发线：A 云端运行与运行基础。计划 6.3 节。102 有推送和恢复，103 让向量随代次上传、有建向量上限，104 有租约和 `datumoj schedule due`。工作流尽量薄：逻辑写在可测试的 `datumoj cloud run` 里，工作流只准备环境再调用它。真实运行（建 bucket、放 secrets、第一次代次、日本站点从 Actions 能否访问）由人在 H0 / H3 做，本任务不连真实 R2。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-105/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `datumoj cloud run [--work-dir DIR] [--max-embed N] [--max-minutes M]`（`cloud.py`）：取得租约（拿不到就退出码 0 并输出“跳过”）→ 恢复当前有效代次到空的工作目录（没有代次时从空状态开始）→ `schedule due` 选出来源 → 逐个运行（抓取、解析、建索引）→ 租约续期 → 推送新代次（发布前校验租约）→ 无论成败都释放租约。超过时间预算时停止开始新来源，已完成的照常发布。任何一步失败：不发布，释放租约，退出码非 0，输出 JSON 摘要（来源、计数、失败原因、代次号）。
2. `.github/workflows/cloud-run.yml`：触发器只有 `schedule`（每小时）和 `workflow_dispatch`（不响应 `pull_request` 和 fork）；`concurrency` 组固定、不取消正在运行的；`permissions: contents: read`；超时上限；用 uv 按 `uv.lock` 安装；安装 Ollama（固定版本）并缓存 bge-m3 模型；凭证和存储位置都只从 secrets 读（`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`，见要求 5（i））；不打印环境变量，不上传数据目录作为 artifact。所用第三方 action 固定到版本。
3. 测试 `tests/test_cloud_run.py`（moto + 本地假来源）：空状态第一次运行发布第 1 代；第二次只跑到期来源并发布第 2 代；租约被占时跳过；某来源失败、推送失败、时间预算用完三种情况的发布与租约行为；两轮之间本机 `datumoj pull` 拿到的结果与工作目录一致。
4. 测试 `tests/test_cloud_workflow.py`：解析工作流 YAML，断言触发器、concurrency、permissions、超时、secrets 只出现在 env 引用里、没有 `pull_request` / `pull_request_target`、没有打印环境的步骤、action 都固定了版本。
5. （2026-10-23 第 1 版审查后补充，人定打回，**取代要求 1 里“任何一步失败：不发布”的说法**）**单个来源失败不阻断发布**：（a）某个来源的抓取、解析或建索引失败时，只在 `runs.jsonl` 记这次失败（`state=final`、`exit_status` 非 0、失败计数和固定原因，`status` 与失败一致，不要写成 completed），继续跑其余到期来源；已完成的来源和失败来源在失败前已写入的内容照常进入本轮代次（107 已保证解析失败的内容不会被发布成 current），本轮照常发布。（b）只要有来源失败，命令退出码非 0（让 Actions 显示失败），JSON 摘要列出失败的来源和原因；发布成功时 `generation` 照常填。（c）只有租约（取得、续期、校验）、恢复、组件初始化、读取到期来源、维护、发布这些整轮性的步骤失败时才整轮不发布。时间预算用完仍是停止开始新来源、已完成的照常发布。（d）不调用 `Scheduler` 的私有方法（如 `_append`）；需要追加运行记录就用公开接口，没有就在 `core/schedule.py` 里加一个最小的公开方法（本条允许改 `core/schedule.py`，只加这个方法）。（e）工作流下载 Ollama 后校验 sha256，值固定写在工作流里：v0.6.8 `ollama-linux-amd64.tgz` 为 `9f9ff52b8297d6e40904f22bac09eac587a05111762bf278359621fbdb53dbb9`（官方 release 的 sha256sum.txt），不符则失败；`tests/test_cloud_workflow.py` 断言有这一步。（f）测试：三个到期来源中一个失败时，另外两个照常发布、失败来源的运行记录正确、退出码非 0、`last_successful_runs` 不含失败来源；租约续期失败和发布失败时仍整轮不发布、租约释放。
6. （2026-10-23 第 2 次尝试后补充）**两处 scope 外阻塞的处理，本条允许**：（g）`core/state.py` 推送前的检查现在拒绝任何 `state != 'published'` 的快照，这是 102 的错误：`failed` 是终态（107 保证失败的内容不会成为 current），真实数据里一定有，必须允许；只有 `staged`（发布做了一半）才阻止推送。只改这一处判断并在 `tests/core/test_state.py` 补用例（有 failed 快照时能推送、有 staged 时拒绝），`core/state.py` 其他部分不动。（h）云端运行在推送前做一次恢复扫描（复用 107 已有的 staged 恢复，不另写一套）；恢复后仍有 staged 时本轮不发布（整轮性失败，原因写清），这属于存储写入的系统性问题。（i）GitHub Actions 会在日志的步骤头里显示该步骤 `env` 的值，`vars` 的值是明文、`secrets` 会被遮盖。R2 的 endpoint 含账号信息，所以 `R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX` 一律从 `secrets` 读，不用 `vars`；`tests/test_cloud_workflow.py` 断言工作流里没有 `vars.` 引用、这些变量都来自 `secrets`。
7. 测试断言：命令输出和日志里不出现测试用的凭证值、endpoint 和 bucket 名。
8. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不连真实 R2，不在本任务里触发工作流。
- 不改已有 CI（`.github/workflows/ci.yml`）。
- 不做监控和周报上云（601）。
