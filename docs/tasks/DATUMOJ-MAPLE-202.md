---
id: DATUMOJ-MAPLE-202
title: 解析缓存与快照发布分开（D-01）：按上下文指纹复用解析产物，每个成功快照都登记并选 current
project: DATUMOJ
status: todo
priority: 1
depends_on: ["DATUMOJ-MAPLE-002"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/pipeline.py", "tests/test_pipeline_identity.py", "tests/test_pipeline.py", "tests/test_pipeline_retry.py", "tests/test_pipeline_content_state.py", "tests/test_evidence_regressions.py", "results/DATUMOJ-MAPLE-202/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", ".github/**", ".env.example"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline_identity.py
    - cmd: make check T=tests/test_pipeline.py
    - cmd: make check T=tests/test_evidence_regressions.py
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-202/summary.md 完成说明"]
  review: {by: human, checklist: ["复用条件是否包含上下文指纹", "复用时是否仍登记快照并单调选 current", "重复抓取是否不重解析、不产生变化", "调整的已有断言是否都有理由", "死链查找是否不再全表扫描", "是否越界"]}
  human: required
max_attempts: 2
---
# 目标
同样的字节出现在另一个资源上时也会被解析和检索；同一资源内容从 A 变 B 又变回 A 时，当前版本回到 A。重复抓取到相同内容时不重复解析。

# 背景
开发线：B 文档线。审核 D-01：`parse_pending`（`pipeline.py`）用 `(sha256, parser, version)` 判断是否已解析，相同就跳过，不看资源和快照上下文；而解析器结果依赖上下文（法令的 role / law_id、PDF 的 doc_key 由 URL 派生，见 `parsers/xml_law.py`、`parsers/pdf.py`）。契约（MAPLE-002）已提供 `parse_context_fingerprint`、按指纹的 `get_parse` / `record_parse`、`SnapshotOrder`、`ChunkStore.publish_current`。另外并入 CodeRabbit 在 #15 指出的问题：`_record_fetch_changes` 对每个死链遍历来源的全部变化日志和全部 chunk。

计划见 `docs/plans/0.3-maple.md`（第 6.0 节是本检查点 m0 的问题表，总计划 `docs/PLAN.md`）。本版本在 `feature/0.3` 分支上开发：任务分支从它拉出，合并回它。
- **只依赖契约**：数据模型在 `src/datumoj/core/models.py`，接口协议在 `src/datumoj/core/contracts.py`，假实现和契约测试在 `src/datumoj/testing/`。身份与状态的约定见 `docs/adr/0004-evidence-identity.md`（由 MAPLE-002 写出）。除契约外，只能 import 已有模块和本任务 `depends_on` 里已合并任务的模块。
- 审核问题的回归用例在 `tests/test_evidence_regressions.py`（MAPLE-002 写出，修好之前标 `xfail(strict=True)`，原因里写着负责的任务号）。修好本任务负责的用例后**删掉它们的 xfail 标记**，不改断言；其他任务的用例不动。
- 测试全部离线：不访问网络，HTTP 一律用 `tests/fixtures/` 里的录制样本或本地假服务器（`pytest-httpserver`）；S3 一律用 moto。
- 依赖已锁定在 `uv.lock`，不能新增依赖，也不能改 `pyproject.toml`。确实需要时写进 `results/DATUMOJ-MAPLE-202/summary.md` 并停下来问。
- `tests/fixtures/` 下已有的样本只读；需要新样本就新建文件。
- 需要改契约（models.py、contracts.py、testing/）时不要自己改（本任务明确允许的除外），写进 summary 并停下来问。
- 测试不能写入仓库里受 git 跟踪的路径；临时文件一律放 `tmp_path`。
- 仓库是公开的：代码、测试、文档里不写账号、密钥、本机绝对路径。

# 要求
1. `parse_pending` 对每个成功快照算上下文指纹；`(sha256, parser, version, 指纹)` 已有成功记录时复用（不再调用解析器），否则解析。
2. 复用时仍要**登记这个快照**：找到该指纹对应的已写入文档版本（同 sha），按快照顺序调用 `publish_current`，使 A→B→A 后 current 为 A；同 URL 重复抓到相同内容（新快照、同 sha、同指纹）不重解析、不产生变化事件，`ParseSummary.skipped` 计数含义不变（复用即计为 skipped）。
3. 不同资源同字节（指纹不同）各自解析、各自有文档和检索块。
4. `_record_fetch_changes`：变化日志和 chunk 的查找移出逐 URL 循环（每次调用至多各读一次，或改用按 sha / URL 的定向查询），结果与现在一致；测试断言大量死链时读取次数不随死链数增长。
5. 新测试 `tests/test_pipeline_identity.py` 覆盖：同字节不同 URL、A→B→A、重复抓取不重解析、法令同字节不同 role 的 meta、指纹不同的快照各自写入、死链查找次数。
6. `tests/test_pipeline.py`、`tests/test_pipeline_retry.py`、`tests/test_pipeline_content_state.py` 中依赖“同 sha 即跳过”的断言（如 `skipped` 计数）允许按新规则调整，在 summary 里逐条说明；不删测试。删掉 `tests/test_evidence_regressions.py` 里本任务负责的用例（xfail 原因里写着本任务号）的 xfail 标记，它们必须通过。全部已有测试（`make check T=tests`）必须仍然通过。

# 不做
- 不做跨库的发布点和故障恢复（MAPLE-107）。
- 不改解析器本身。
