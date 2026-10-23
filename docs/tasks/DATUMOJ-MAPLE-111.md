---
id: DATUMOJ-MAPLE-111
title: 向量索引有界写入与代际重建（D-11，本机部分）
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-107"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/index/vector.py", "src/datumoj/pipeline.py", "tests/index/test_vector_generations.py", "results/DATUMOJ-MAPLE-111/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_pipeline_vectors.py"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_vector_generations.py
    - cmd: make check T=tests/index
    - cmd: make check T=tests/test_pipeline_vectors.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-111/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
向量追加不再每次读整张表；重建向量索引时先建新一代、校验后再切换，重建中途失败或中断时旧一代仍可查询。

# 背景
开发线：A 云端运行与运行基础。审核 D-11：`LanceVectorIndex.add`（`index/vector.py`）每次 `to_arrow()` 整张表取 hash 列；`reindex`（`pipeline.py`）先把全部 chunk 装进列表，再 `clear` 旧索引后逐批添加，中途失败就没有可用索引。向量状态文件和 vector missing 记录见 `pipeline.py` 的 `_load_vector_state` 等。R2 上的向量（MAPLE-101、103）以后在这个代际结构上做。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-111/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `add` 只查询本批 chunk 的 `chunk_id` / 哈希（LanceDB 的过滤查询，按批分段，单次查询的 ID 数有上限），不读整张表；结果与现在一致（已存在且哈希相同的跳过）。
2. 代际：向量表按代命名（如 `chunks_g<N>`），当前代记在索引目录的元数据里；`reindex(vectors_only=True)` 和全量重建流式读取 chunk（不整份装进内存），写入新一代，完成后校验（行数与可检索块数一致、模型和维度一致），校验通过才原子切换当前代，然后删除更旧的代（保留上一代一份，便于回退）。
3. 重建中途抛错或进程中断：当前代不变、仍可查询；残留的未完成代在下次重建开始时清理。
4. 打开旧格式（无代际元数据）的索引时视为第 0 代继续可用，不需要手动迁移。
5. 新测试 `tests/index/test_vector_generations.py` 覆盖：追加不调用整表读取（用计数或替身断言）、重建成功切换、重建中途失败旧代可查、残留代清理、旧格式兼容。`tests/test_pipeline_vectors.py` 必须仍然通过。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不上 R2、不做 ANN 索引参数调优（MAPLE-101、103）。
- 不换嵌入模型。
