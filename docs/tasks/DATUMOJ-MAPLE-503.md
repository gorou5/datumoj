---
id: DATUMOJ-MAPLE-503
title: 管线覆盖状态（D-13）：区分抓到、已解析、可检索、索引重建中和真正零命中
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-107", "DATUMOJ-MAPLE-502"]
runner: codex
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/index/coverage.py", "src/datumoj/index/hybrid.py", "src/datumoj/analysis/tools.py", "src/datumoj/admin/web.py", "src/datumoj/cli.py", "tests/index/test_pipeline_coverage.py", "results/DATUMOJ-MAPLE-503/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_admin.py"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_pipeline_coverage.py
    - cmd: make check T=tests/index
    - cmd: make check T=tests/analysis
    - cmd: make check T=tests/test_admin.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-503/summary.md 完成说明"]
  review: {by: other_family, checklist: ["是否越界", "测试是否覆盖每条要求", "是否通过了对应的契约合格测试", "是否有联网的测试", "是否只依赖契约而没有 import 其他线的实现"]}
  human: sampled
max_attempts: 2
---
# 目标
某个来源的资料已经抓到但还没解析、解析失败、需要 OCR 或索引还没建好时，检索结果说“还不能检索”并给出管线状态，不再报告为真正的零命中；CLI、MCP 和管理页面用同一口径。

# 背景
开发线：E 检索线。审核 D-13：`gap_state`（`index/coverage.py`）只看注册表、manifest 的抓取结果和命中数，来源只要成功抓过且没命中就判 `zero_hits`，不看解析和索引。契约（MAPLE-002）已提供 `GapState.not_indexed`、`PipelineStatus` 和 `ToolResult.pipeline`。解析状态见 `Manifest.parse_stats`，可检索块数见 ChunkStore / 关键词索引，向量代次见 MAPLE-111 的元数据。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核（计划 6.0 节）的合成复现由负责修复的任务写成回归测试，放在 `tests/regressions/test_audit_<任务号>.py`，断言修好以后的正确行为。调度器不接受 `skip` / `xfail`，回归测试必须在本任务里直接通过。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-503/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. 按来源计算 `PipelineStatus`：可解析的成功快照数、已解析、解析失败、needs_ocr、可检索块数、索引代次（或“重建中”）。
2. 零命中时：被查询的来源有成功快照但可检索块数为 0、或有未解析 / 失败 / needs_ocr 的快照占多数（规则写成常量并测试），判 `not_indexed` 并在 `gap_detail` 说明；只有全部已解析且已建索引仍没命中才是 `zero_hits`。未指定来源时按全部来源汇总。
3. `search_docs` 等检索类工具在结果里带 `pipeline`；`datumoj coverage`（或现有的覆盖命令）和管理页面显示同样的数字。
4. 新测试 `tests/index/test_pipeline_coverage.py` 覆盖：未解析、解析失败、needs_ocr、索引重建中、真正零命中、多来源汇总，以及 CLI 和管理页面的数字与工具一致。`tests/test_admin.py` 必须仍然通过。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不改抓取和解析本身。
