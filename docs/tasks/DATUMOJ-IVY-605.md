---
id: DATUMOJ-IVY-605
title: 修复：解析失败的快照下次要重试，不再当作已处理
project: DATUMOJ
status: todo
priority: 1
depends_on: []
runner: claude
model: null
tier: medium
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/pipeline.py", "tests/test_pipeline_retry.py", "results/DATUMOJ-IVY-605/**"]
out_of_scope: [".orch/**", "docs/**", "CLAUDE.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**"]
protected: ["tests/fixtures/**", "src/datumoj/core/models.py", "src/datumoj/core/contracts.py", "src/datumoj/testing/**", "tests/test_pipeline.py"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline_retry.py
    - cmd: make check T=tests/test_pipeline.py
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-IVY-605/summary.md 完成说明"]
  review: {by: other_family, checklist: ["失败记录是否会被重试、成功记录是否仍然跳过", "重试成功后解析记录是否更新为成功", "反复失败时是否有上限或明确报告，不会静默", "原有 601 的测试是否全部仍然通过", "是否越界"]}
  human: sampled
max_attempts: 2
---
# 目标
解析失败（`ParseStatus.failed`）的快照在下一次 `parse_pending` 时会被重新解析；只有解析成功（`ok`）或确认没有解析器（`no_parser`）的快照才算“已处理”。

# 背景
开发线：F 入口线（修复 DATUMOJ-IVY-601）。集成任务 DATUMOJ-IVY-901 发现：`src/datumoj/pipeline.py` 第 84 行只检查 `get_parse(...) is not None` 就跳过，失败记录也被当成已处理，所以修好解析器之后，之前失败的快照永远不会重新解析；紧接失败 parse 的 `index --rebuild` 也会退出 0，掩盖了失败。

计划见 `docs/PLAN.md`；测试全部离线，不能改依赖。

# 要求
1. 已有解析记录且 `status == failed` 时重新解析；`ok` 和 `no_parser` 照旧跳过。
2. 重试成功后，解析记录更新为 `ok`（用 `Manifest.record_parse` 覆盖或追加，以 `get_parse` 读到的是最新状态为准；不能改契约）。
3. 汇总里分开计数：本次新解析、重试成功、仍然失败；仍然失败的列出快照和错误原因，`parse` 命令照旧以非零退出码报告失败。
4. 原有 `tests/test_pipeline.py` 已列为 protected，必须全部通过。
5. 新测试 `tests/test_pipeline_retry.py`（用 `datumoj.testing.fakes`）：一个先失败、修好解析器（换版本相同、行为改正的假解析器）后重试成功的快照；一个一直失败的快照两次都被报告；成功的快照第二次不再解析。

# 不做
- 不改解析器、索引、契约。
