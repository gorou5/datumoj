---
id: DATUMOJ-MAPLE-901
title: 检查点 m1：云端运行端到端（moto 上两轮云端运行 + 本机拉取 + MCP 回答一致）；真实运行由人记录
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-900", "DATUMOJ-MAPLE-103", "DATUMOJ-MAPLE-105", "DATUMOJ-MAPLE-106", "DATUMOJ-MAPLE-601"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["tests/integration/test_maple_m1.py", "src/datumoj/cli.py", "src/datumoj/wiring.py", "src/datumoj/mcp_server.py", "results/DATUMOJ-MAPLE-901/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/integration/test_maple_m1.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-901/summary.md 完成说明"]
  review: {by: human, checklist: ["离线端到端是否覆盖两轮发布、拉取、MCP 一致、租约、失败、提醒、周报", "真实运行清单是否完整且不含凭证值", "是否只改了接线、CLI 和集成测试", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
云端运行的整条链在离线环境里跑通并有证据：两轮 `cloud run` 各发布一个代次，本机 `pull` 后 MCP 的检索、引用、`query_sql` 与云端工作目录一致，提醒和周报正常；人按 H0 / H3 在真实 R2 和 Actions 上照做一遍并记录。

# 背景
开发线：集成。MAPLE 检查点 m1（计划第 3 节目标 1–3）。真实运行（建 bucket、放 secrets 和 variables、首次迁移：本机数据副本 replay 到新目录、建全量向量、作为第 1 代上传、在 Actions 上手动触发一次、确认日本站点可访问）由人做，结果记到 `docs/acceptance/v0.3.0-maple-m1.md`。只允许改接线、CLI、MCP 说明文字和新的集成测试。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-901/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 集成测试 `tests/integration/test_maple_m1.py`（moto，合成来源，含向量用假嵌入）：第一轮 `cloud run` 从空状态发布第 1 代；推进时间后第二轮只跑到期来源并发布第 2 代，第二轮上传量明显小于第一轮；本机 `pull` 后用 MCP 工具（`search_docs`、`cite`、`query_sql`、`version`）回答，与云端工作目录一致，`version` 报第 2 代；租约被本机占用时云端那一轮跳过；中途注入失败的一轮不改变有效代次；停掉一轮后出现云端缺口提醒；周报对象已写。
2. summary 写给人的真实运行清单：H0 要建的 bucket、secrets、variables 的名字（不写值），H3 首次迁移的命令顺序（`diagnose evidence` → `replay --vectors` → 比对 → `state push`），第一次手动触发工作流后要核对的项目，以及本机只读 token 的用法。
3. （固定要求）集成测试**不能写入仓库里受 git 跟踪的路径**（包括 `results/`）：证据默认写到临时目录，只有设置了环境变量 `DATUMOJ_EVIDENCE_DIR` 时才写到该目录（参考 `tests/integration/test_law_e2e.py` 的写法）。
4. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改各开发线的模块（接线、CLI 和 MCP 说明文字除外）。
- 不连真实 R2，不触发真实工作流；真实运行由人做。
