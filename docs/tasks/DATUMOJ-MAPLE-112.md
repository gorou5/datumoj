---
id: DATUMOJ-MAPLE-112
title: 状态副本按块增量：SQLite 副本切成 256KB 块按内容寻址；关键词索引不进代次、恢复时从 chunks 重建
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-102", "DATUMOJ-MAPLE-103", "DATUMOJ-MAPLE-105"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/core/state.py", "src/datumoj/cli.py", "docs/adr/0005-state-generations.md", "tests/core/test_state_blocks.py", "tests/core/test_state.py", "tests/test_cloud_run.py", "results/DATUMOJ-MAPLE-112/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example", "docs/PLAN.md", "docs/TASKS.md", "docs/plans/**", "docs/tasks/**", "docs/acceptance/**", "docs/references/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_state_blocks.py
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests/core/test_state_vectors.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-112/summary.md 完成说明"]
  review: {by: human, checklist: ["分块、拼回、校验是否字节级一致", "增量是否成立（只传变化的块）", "关键词索引重建是否只用已发布的块、校验后才交出目录", "旧格式代次能否恢复", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
每小时的状态代次只新增真正变化的数据，而不是把整份 SQLite 再存一遍；R2 上的存储增长接近数据本身的增长。

# 背景
开发线：A 云端运行与运行基础。102 的代次以整个文件为单位按内容寻址：没变的文件复用，但 SQLite 只要改一行就整份再存。按本机数据（chunks.sqlite 约 700 MB、keyword.sqlite 约 1.2 GB）和 JMA 每小时约 67 份文档估算，每小时新增约 2 GB、每月约 1.4 TB，且旧代次不删。2026-10-23 在本机数据副本上实测（用真实的 `SqliteChunkStore` / `SqliteKeywordIndex` 写入路径模拟 24 小时、每小时 67 份文档，半数是已有文档的新版本，每小时用 backup API 做一致副本）：24 小时整文件累计 47.6 GB；按 256 KB 块去重后 chunks.sqlite 只新增 0.29 GB（每小时中位数 9 MB），而 keyword.sqlite 仍新增 2.75 GB（FTS5 段合并使改动分散到整个文件，峰值一小时 513 MB）；同期数据本身只增长 233 MB。keyword.sqlite 完全可由 chunks.sqlite 重建：本机全量重建 27,880 块用时 65 秒。人据此定：SQLite 副本按块增量，关键词索引不进代次，恢复时重建。向量（103）的 Lance 数据文件本身不可变，已按文件内容寻址，保持不变。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-112/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. SQLite 副本（manifest、chunks、catalog、changes 等，backup API 得到的一致副本）按 256 KB 固定大小切块（页大小 4096 的整数倍，对齐页边界），每块按 SHA256 内容寻址存为 `copies/sha256/<sha>`；清单里每个库给出有序的块列表和整文件的 SHA256、字节数（用现有 `StoreCopy` 表达：每块一条，逻辑名形如 `<库名>.blocks/<序号>`，格式标明是分块；另加一条整文件校验信息。若现有模型确实表达不了，写进 summary 停下来问，不要改 `models.py`）。远端已有的块不上传（沿用 102 的一次 `list` 判断）。小文件（runs.jsonl、control.json 等）和 Parquet 照旧整文件。
2. 关键词索引 `keyword.sqlite` 不进代次。恢复时先按块拼回各库并校验整文件 SHA256，再从恢复出的 chunks 里**当前已发布的块**重建关键词索引（复用现有 `reindex` / `SqliteKeywordIndex`，不另写一套），重建完成并通过 102 已有的出处抽样校验后才交出目录。推送时仍检查本地关键词索引不含未发布的块（102 的已有检查保留）。
3. 兼容：能恢复 102 / 103 格式（整文件副本、含 keyword.sqlite）的旧代次；新推送一律用新格式。
4. 更新 ADR 0005：副本布局加上分块规则、关键词索引不进代次而在恢复时重建的理由（引用上面的实测数字），以及恢复耗时的影响。
5. 测试 `tests/core/test_state_blocks.py`：分块和拼回的字节完全一致；只改少量行后第二次推送只上传少数块（用计数的假存储断言上传块数远小于总块数）；篡改一个块时恢复失败且不交出目录；恢复后关键词检索结果与原目录一致；旧格式代次仍能恢复；没有 keyword.sqlite 的代次在 `raw=remote` 模式下也能恢复并检索。
6. 测试断言：命令输出和日志里不出现测试用的凭证值、endpoint 和 bucket 名。
7. 全部已有测试（`make check T=tests`）必须仍然通过。

8. （2026-10-23 补充，前两次尝试因此失败）`tests/core/test_state.py` 和 `tests/test_cloud_run.py` 里有断言写死了 102/105 的整文件格式，本任务可以改它们，但**只改这些写死旧格式的断言**，其他断言不删不放宽：
   - `test_state.py::test_no_change_push_lists_once_without_content_download_or_raw_read` 断言 `stores` 的逻辑名集合：改成按新格式断言（各 SQLite 库是 `<库名>.blocks/<序号>` 加整文件校验条目，不含 `keyword.sqlite`，`observations.duckdb` 等照旧），“无变化时只 list 一次、不下载内容、不读 raw”的断言保持不变。
   - `test_cloud_run.py` 的 `assert_pulled` 逐行比较 `keyword.sqlite` 的 `entries` 表（含 rowid）：关键词索引现在是恢复时重建的，rowid 和行序不保证一致，改成比较不含 rowid 的内容集合（或比较同一组查询的检索结果）；`manifest.sqlite`、`chunks.sqlite` 的逐行比较保持不变。
   - 不要为了让这些测试通过而改变要求 1–2 的格式。

# 不做
- 不删除 R2 上的任何对象（旧代次保留和清理另定）。
- 不改向量的存法。
- 不改 `models.py`、`contracts.py`。
