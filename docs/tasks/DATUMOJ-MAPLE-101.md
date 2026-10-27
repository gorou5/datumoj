---
id: DATUMOJ-MAPLE-101
title: 向量库上 R2 的验证：本机向量库体积构成（只读诊断）；LanceDB 读写对象存储的可行性；结论写进 ADR 0002
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/diagnose.py", "src/datumoj/cli.py", "tests/test_diagnose_storage.py", "tests/index/test_vector_object_storage.py", "docs/adr/0002-network-storage.md", "results/DATUMOJ-MAPLE-101/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example", "docs/PLAN.md", "docs/TASKS.md", "docs/plans/**", "docs/tasks/**", "docs/acceptance/**", "docs/references/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_diagnose_storage.py
    - cmd: make check T=tests/index/test_vector_object_storage.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-101/summary.md 完成说明"]
  review: {by: human, checklist: ["诊断是否真正只读", "实验是否在锁定版本上做、结论是否与测试观察一致", "三种做法的比较是否有数据和依据", "是否改了向量索引实现", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
弄清向量库怎么上 R2：本机向量库为什么有 15G、能压到多少；锁定版本的 LanceDB 能不能直接读写 S3 类存储；然后在“直接读写 R2”“按来源分表、只拉当次要写的来源”“每代次整份上传”之间给出有依据的建议，由人在审查时拍板。

# 背景
开发线：A 云端运行与运行基础。计划 6.2 节：本机向量库现有约 15G，先查是否是没压缩的历史版本；每次 Actions 运行不应整份下载和上传。m0 的 111 已经让向量索引按代建表（`index/vector.py` 的 `begin_generation` / `finish_generation` / `_cleanup_generations`），追加只查本批哈希。ADR 0002 的 LanceDB 一节指出：`LanceVectorIndex` 先构造 `Path` 并创建本机目录，`_meta.json`、`vectors-state.json` 也在本机，不能只把路径改成 `s3://`。真实数据只能由人在本机运行诊断：本任务写只读诊断命令，并在合成数据上测试；实验只在 moto 上做（moto 的 `ThreadedMotoServer` 可以给 LanceDB 提供本地 HTTP 的 S3 endpoint）。锁定版本：lancedb 0.40.0。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-101/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `datumoj diagnose storage [--data-dir DIR]`（加在 900 写的 `diagnose.py`）：**只读**，输出 JSON：数据目录下各库和目录的字节数；向量库每张表的行数、版本数、数据文件数和字节数、历史版本和已删除数据占用的字节数、各代表（111 的代次表）分别多大；估算执行 LanceDB 的压缩和清理旧版本后能省多少（只估算，不执行）。运行前后数据目录的文件内容不变（测试断言，沿用 `tests/test_diagnose.py` 的做法）。
2. 可行性测试 `tests/index/test_vector_object_storage.py`：用 `ThreadedMotoServer` 起本地 S3，按锁定版本的 LanceDB 对 `s3://` 表做建表、追加、按条件删除、检索、两个连接并发提交、提交中途中断后重开；每项断言实际观察到的行为（能用的断言能用；不支持的断言它确实报错或不一致，并在 ADR 里写清）。不改 `index/vector.py`。
3. 在 ADR 0002 末尾加“补充（MAPLE-101，日期）”一节：本机体积构成的结论（用合成数据说明诊断口径，真实数字留空待人运行后填）；LanceDB 对象存储实验的逐项结果；三种做法（直接读写 R2、按来源分表只拉要写的来源、每代次整份上传）各自的下载 / 上传量、并发写入风险、恢复方式、对 `LanceVectorIndex` 的改动量；给出建议和理由。也评估 SQLite 库（约 1.8G）是否需要按来源拆分，只写结论，不改。
4. summary 里写给人的操作说明：在本机数据上运行 `datumoj diagnose storage` 的命令，以及根据结果在审查时要做的决定。
5. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改 `index/vector.py` 和向量的读写路径（103 按结论实现）。
- 不执行任何压缩或清理。
- 不碰真实数据目录，不连真实 R2。
