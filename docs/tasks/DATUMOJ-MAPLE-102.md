---
id: DATUMOJ-MAPLE-102
title: 状态代次：各库一致副本、增量上传、发布清单、恢复到新目录并校验（datumoj state push / restore）
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-001"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/state.py", "src/datumoj/core/state_s3.py", "src/datumoj/cli.py", "src/datumoj/wiring.py", "tests/core/test_state.py", "tests/core/test_state_s3.py", "tests/integration/test_state_roundtrip.py", "results/DATUMOJ-MAPLE-102/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests/core/test_state_s3.py
    - cmd: make check T=tests/integration/test_state_roundtrip.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-102/summary.md 完成说明"]
  review: {by: human, checklist: ["副本是否一致（backup API、同一时刻）", "是否先传副本、最后发布，失败代次不会成为恢复入口", "增量上传是否成立", "恢复是否校验 SHA 并抽样还原出处", "日志是否泄露凭证、endpoint、bucket", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
本机或 Actions 上的一份数据目录能做成一个一致的状态代次推到 R2，也能从 R2 恢复到一个新目录并校验通过；中途失败不会留下可被当成有效代次的半截状态。

# 背景
开发线：A 云端运行与运行基础。契约见 001 写的 `StateGeneration`、`StateObjectStore` 和 ADR 0005。raw 已有 S3 实现（`core/rawstore_s3.py`，键为 `raw/<前两位>/<sha256>`），本任务的 S3 实现沿用它的客户端配置和错误处理写法（不暴露 SDK 错误内容和凭证）。向量不在本任务里上传（103 按 101 的结论做），代次的向量覆盖照实记录：没有上传向量时为空。写入租约由 104 做，本任务的推送接受一个可选的“发布前检查”回调，104 用它接上租约。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-102/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `state_s3.py`：`StateObjectStore` 的 S3 实现，通过 001 的契约合格测试套件（moto）。
2. `state.py` 的推送：在本机临时目录做一致副本——SQLite 各库用 backup API；`observations.duckdb` 和 `parquet/` 按 ADR 0002 的 Parquet 一节处理（Parquet 文件作为不可变对象；duckdb 文件若只是视图就在恢复时重建，否则导出）；`runs.jsonl`、`control.json` 等小文件原样。每个副本按内容 SHA256 寻址上传，**远端已有的对象不再上传**（增量）；raw 后端是本机时，把远端没有的 raw blob 按 `raw/<前两位>/<sha256>` 补传。全部上传完后才写代次清单和切换指针（按 ADR 0005 的条件写入）；推送前检查数据目录没有正在运行的抓取（`locks/` 下的来源锁），有就拒绝。
3. `state.py` 的恢复：把指定代次（默认当前有效代次）恢复到**一个空的新目录**，逐个校验 SHA256；打开各库检查 schema 能用；抽样还原出处（随机取若干块和观测值，用现有的引用和出处解析能还原到快照和 raw），输出校验报告。不切换任何目录（切换是 106 的事）。
4. CLI：`datumoj state push [--data-dir]`、`datumoj state restore --to DIR [--generation N]`、`datumoj state list`（列出代次号、生成时间、写入者、副本数和总字节数）。输出 JSON。
5. 故障注入测试：上传副本中途失败、写清单失败、切换指针时被另一个写入者抢先，三种情况下当前有效代次都不变，下一次推送能正常完成；恢复时副本被篡改（SHA 不符）则报错、不产出可用目录。
6. 往返集成测试 `tests/integration/test_state_roundtrip.py`：用合成来源跑一遍抓取、解析、建关键词索引，推送；在另一个空目录恢复；恢复后 `search`、`cite`、`query_sql` 的结果与原目录一致；再改动一个来源推送第二代，断言第二次只上传了变化的对象。
7. （固定要求）集成测试**不能写入仓库里受 git 跟踪的路径**（包括 `results/`）：证据默认写到临时目录，只有设置了环境变量 `DATUMOJ_EVIDENCE_DIR` 时才写到该目录（参考 `tests/integration/test_law_e2e.py` 的写法）。
8. （2026-10-23 第 1 版审查后补充，人定打回）**传输量要求**：本机现有 raw 约 4.7 GB、13,000 个对象，Actions 每小时运行一次，推送和恢复都不能按全量传输。（a）判断远端是否已有：每次推送对 `copies/sha256/` 和 raw 前缀各做一次 `list`（分页），按键集合判断，**不得为判断存在而 `get` 下载对象内容**；内容寻址的键存在即视为该内容已在（写入时已校验）。（b）上传后不整份读回校验：用服务端校验（S3 的 `ChecksumSHA256` 或 Content-MD5，moto 支持的那种）保证写入字节正确；`put_if_absent` 冲突时同理只按键判断，不下载。（c）推送不再把全部 raw 复制到临时目录：raw 是不可变的内容寻址文件，只对远端没有的 blob 从本机目录读出、校验哈希后上传；manifest 引用但本机和远端都没有的 raw 才报错。（d）恢复加 `raw` 模式：`restore(target, generation=None, *, raw="local" | "remote")`，CLI `--raw local|remote`，默认 `local`（行为同现在）；`remote` 时不下载 raw，只用一次 `list` 核对 manifest 引用的 raw 都在远端，恢复出的目录配置为从 R2 读 raw（沿用 `S3RawStore`），校验时不读 raw 内容；云端运行（105）用 `remote`。（e）测试用计数的假存储断言：第二次推送（无变化）不调用任何内容下载，只有 `list`；只改一个来源时只上传变化的对象且不下载；`remote` 恢复不下载 raw；raw 缺失时 `remote` 恢复报错。
9. 测试断言：命令输出和日志里不出现测试用的凭证值、endpoint 和 bucket 名。
10. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不做向量上传（103）。
- 不做写入租约和到期来源选择（104）。
- 不做本机切换和 `datumoj pull`（106）。
- 不删除 R2 上的任何对象（旧代次保留期由人另定）。
