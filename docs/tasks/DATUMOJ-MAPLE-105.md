---
id: DATUMOJ-MAPLE-105
title: 云端运行：datumoj cloud run（租约 → 恢复 → 跑到期来源 → 维护 → 发布 → 释放）和 GitHub Actions 工作流
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-102", "DATUMOJ-MAPLE-103", "DATUMOJ-MAPLE-104"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/cloud.py", "src/datumoj/cli.py", ".github/workflows/cloud-run.yml", "tests/test_cloud_run.py", "tests/test_cloud_workflow.py", "results/DATUMOJ-MAPLE-105/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".env.example", ".github/workflows/ci.yml"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_cloud_workflow.py
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
2. `.github/workflows/cloud-run.yml`：触发器只有 `schedule`（每小时）和 `workflow_dispatch`（不响应 `pull_request` 和 fork）；`concurrency` 组固定、不取消正在运行的；`permissions: contents: read`；超时上限；用 uv 按 `uv.lock` 安装；安装 Ollama（固定版本）并缓存 bge-m3 模型；凭证只从 secrets 读（`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`），`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET` 从 variables 读；不打印环境变量，不上传数据目录作为 artifact。所用第三方 action 固定到版本。
3. 测试 `tests/test_cloud_run.py`（moto + 本地假来源）：空状态第一次运行发布第 1 代；第二次只跑到期来源并发布第 2 代；租约被占时跳过；某来源失败、推送失败、时间预算用完三种情况的发布与租约行为；两轮之间本机 `datumoj pull` 拿到的结果与工作目录一致。
4. 测试 `tests/test_cloud_workflow.py`：解析工作流 YAML，断言触发器、concurrency、permissions、超时、secrets 只出现在 env 引用里、没有 `pull_request` / `pull_request_target`、没有打印环境的步骤、action 都固定了版本。
5. 测试断言：命令输出和日志里不出现测试用的凭证值、endpoint 和 bucket 名。
6. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不连真实 R2，不在本任务里触发工作流。
- 不改已有 CI（`.github/workflows/ci.yml`）。
- 不做监控和周报上云（601）。
