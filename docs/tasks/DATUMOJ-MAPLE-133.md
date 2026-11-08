---
id: DATUMOJ-MAPLE-133
title: 解析循环只在真正要读 raw 时才检查它是否存在，避免 HEAD 次数随标记增长
project: DATUMOJ
status: done
priority: 2
depends_on: ["DATUMOJ-MAPLE-132"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/pipeline.py", "tests/test_pipeline.py", "tests/regressions/test_audit_133.py", "results/DATUMOJ-MAPLE-133/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", ".github/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-133/summary.md 完成说明"]
  review: {by: other_family, checklist: ["已解析且可复用的快照是否不再调用 raw.exists", "标记过且 raw 缺失时，未解析或要重试的快照是否跳过、不记失败", "没有标记却缺失时是否仍然报错", "summary.skipped 是否没有重复计数", "是否越界"]}
  human: sampled
max_attempts: 5
---
# 目标
`parse_pending` 的 HEAD 次数不再随 raw 标记数增长。

# 背景
- 任务 131 的审查发现：`pipeline.py` 的 `parse_pending` 对每个带 raw 标记的快照先调用 `deps.raw.exists`，S3 后端每次是一个顺序 HEAD。
- 标记不会清除，jp-jma 每天约 1,400 个文件，30 天约 4 万个标记。审查按每次 30–50 ms 估算（未实测），单来源要 20–35 分钟，接近 40 分钟的预算，超限后最新的 entry 最后才轮到。
- 这些快照绝大多数已经解析过，结果可复用，根本不读 raw。

依赖 132（都改 `pipeline.py`）。任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. 把标记 raw 的存在检查从循环开头挪到真正要读 raw 的地方，也就是将要调用 `_process` 之前：
   - 无解析器（`cls is None`）的分支和 `reusable` 为真的快照：不调用 `raw.exists`。
   - 需要解析或重试的快照：若 sha256 带标记且 raw 不存在，跳过，不记失败。
   - `summary.skipped` 每个快照最多加一次：前面已因 `previous is not None` 加过的不再重复。
2. 没有标记却缺失 raw 的行为不变，仍然报错。
3. 不改标记规则、清理规则、`rawstore`，不加列表缓存。
4. 测试（用计数的假 raw 存储）：
   - 带标记、已解析且可复用的 N 个快照：`raw.exists` 调用 0 次。
   - 带标记、未解析、raw 已删：跳过，不记失败。
   - 带标记、上次失败要重试、raw 已删：跳过，不记失败。
   - 无标记、raw 缺失：仍然报错。
   - 新增 `tests/regressions/test_audit_133.py`：模拟 1,000 个带标记且已解析的快照，断言一轮 `raw.exists` 调用为 0。
   - 已有的测试和断言全部不变。
5. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不改核心模块、爬虫、解析器。
- 不处理 131 审查提到的其他风险（标记不撤销、本机推送传回已删的 raw 等）。
