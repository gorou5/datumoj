---
id: DATUMOJ-MAPLE-103
title: 向量库定期压缩与清理旧版本；向量随状态代次上传和恢复（按文件内容寻址）；单次运行的建向量上限
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-101", "DATUMOJ-MAPLE-102", "DATUMOJ-MAPLE-106", "DATUMOJ-MAPLE-111"]
runner: codex
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/index/vector.py", "src/datumoj/pipeline.py", "src/datumoj/core/state.py", "src/datumoj/cli.py", "tests/index/test_vector_maintain.py", "tests/core/test_state_vectors.py", "results/DATUMOJ-MAPLE-103/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_vector_maintain.py
    - cmd: make check T=tests/core/test_state_vectors.py
    - cmd: make check T=tests/index/test_vector.py
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-103/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
向量库不再无限膨胀：每次运行后合并碎片、清理旧版本；向量和其他库一起进状态代次，恢复后可直接检索；Actions 上一次运行只给有限数量的新块建向量，没建完的下次继续。

# 背景
开发线：A 云端运行与运行基础。本机诊断（2026-10-23，`datumoj diagnose storage`）：向量库 16.2 GB 中 16.04 GB 是 `_versions/` 下 32,788 个历史版本的元数据，实际向量数据约 140 MB（27,076 行、11,033 个数据文件，其中 10,735 个小碎片），压缩清理后估计约 136 MB。原因是每次写入都提交一个新版本、从不压缩和清理。人据此定：**不按来源分表**；向量库每次运行后压缩碎片、清理旧版本，代次里整份保存（按文件内容寻址，未变的文件不重传）。 101 的实验和取舍写在 ADR 0002 的“补充（MAPLE-101）”。102 的推送和恢复（`core/state.py`）目前不含向量，代次的 `vector_coverage` 为空。111 已让向量按代建表（`begin_generation` / `finish_generation`），107 的 `vectors-state.json` 记录待补向量的块。锁定版本 lancedb 0.40.0。

计划见 `docs/plans/0.3-maple.md`（第 6.1–6.5 节是云端运行的设计，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。检查点 m0（证据正确性）已完成。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。存储边界见 `docs/adr/0002-network-storage.md`，证据身份见 `docs/adr/0004-evidence-identity.md`，状态代次见 `docs/adr/0005-state-generations.md`（由 MAPLE-001 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 / R2 一律用 moto（锁定的 moto 5.2.4 支持 `IfNoneMatch` / `IfMatch` 条件写入）。不连真实 R2。
- R2 凭证只从环境变量 `R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY` 读，endpoint、bucket 沿用 `core/storage.py` 的 `StorageConfig`（`R2_ENDPOINT`、`DATUMOJ_S3_BUCKET`、`DATUMOJ_S3_PREFIX`）。**仓库和 Actions 日志都是公开的**：命令输出、日志、异常信息里不出现凭证、endpoint、bucket 名和原始数据内容，只写计数、来源 ID、代次号和对象键的相对部分。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-103/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `LanceVectorIndex.maintain(retain=timedelta)`：合并小碎片（LanceDB 的 optimize / compact）并清理早于保留期的旧版本（默认 1 小时，可配置，最新版本始终保留，不删其他代次表里仍被当前代引用的数据）；返回前后的版本数、数据文件数和字节数。向量不可用时什么也不做。
2. 在每次 `parse_pending` 结束、且本次写过向量时调用一次 `maintain`；`datumoj index maintain [--data-dir]` 可手动运行（输出 JSON）。维护失败只记告警，不影响已发布的状态。
3. 建向量上限：单次运行最多给 N 个块建向量（配置项，默认不限；Actions 上由工作流设置），超出的块进 `vectors-state.json` 的待补清单，下次运行优先补；关键词检索不受影响。
4. 状态代次包含向量：`state push` 在一致边界内（与其他库同一时刻）先 `maintain`，再把向量表目录的每个文件、`_meta.json`、`vectors-state.json` 作为副本上传（逻辑名 `vectors/<相对路径>`，按内容寻址，远端已有的不再上传）；`vector_coverage` 照实填（模型、版本、维度、已建块数、待补块数）。恢复时重建向量目录，打开表核对行数与覆盖一致，不一致则恢复失败。没有向量（未配置嵌入）时照旧为空。
5. 测试：用合成数据制造很多次小提交后 `maintain`，版本数和数据文件数明显下降、检索结果不变；保留期内的版本不删；推送两代、第二代只追加少量块时只上传变化的向量文件；恢复后向量检索与原目录一致；篡改一个向量副本时恢复失败；建向量上限生效且下次补齐。
6. 全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不按来源分表。
- 不改检索和 RRF 逻辑。
- 不处理本机现有的旧向量库（H3 用 `datumoj replay --vectors` 在新目录重建）。
- 不删除 R2 上的对象。
