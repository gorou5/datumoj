---
id: DATUMOJ-MAPLE-126
title: Ollama 嵌入超时改为对半拆批重发，不再把同一批原样重试到底（长文本批次在 CPU 上必然超时、每批白耗 4 分钟）
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/index/embed.py", "tests/index/test_vector.py", "tests/regressions/test_audit_126.py", "results/DATUMOJ-MAPLE-126/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/**", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/pipeline.py", "src/datumoj/rikolto/cloud.py", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/index/test_vector.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-126/summary.md 完成说明"]
  review: {by: other_family, checklist: ["多条文本的批次超时后是否对半拆开重发，而不是原样重试", "单条文本超时是否仍按 retries 重试并最终抛 EmbeddingError", "拆批后返回的向量是否与输入顺序一一对应", "一次 embed 调用的请求总数是否有上界（不会因为拆批无限增长）", "5xx 和连接错误的原样重试、4xx 不重试、维度校验是否不变", "错误信息是否不含输入文本", "是否改了 embed.py 以外的实现或越界"]}
  human: sampled
max_attempts: 3
---
# 目标
`OllamaEmbedder` 在 Actions 的 CPU 上嵌入长文本时，一批里有长文本导致超时，也能把这一批的内容嵌进去，不再每次白白耗掉约 4 分钟后整批失败。

# 背景
2026-11-02 第一次 `cloud-embed`（第 4 代）：
- 100 分钟只补嵌了 4,384 个文本块，约每秒 1 个。
- 共 159 批（每批 32 个），有 8 批在**约 244.7 秒**后失败，什么都没嵌进去，共耗约 33 分钟。
- 原因：`OllamaEmbedder` 默认 `timeout=60`、`retries=3`、`backoff=0.5`。一批请求超时（`httpx.TransportError` 含超时）后，**同样的 32 条原样重试**，4 × 60 + 0.5 + 1 + 2 = 243.5 秒后抛 `EmbeddingError`。
- 正常的一批要 16 到 50 秒。长文本（法规条文）凑满 32 条时会超过 60 秒，原样重试必然再超时。
- 这 8 篇按 124 的规则会在下一轮再失败；连续 3 次后就被记成彻底失败，永远嵌不进去。
- 被放弃的请求，Ollama 服务端很可能还在继续计算，会拖慢紧接着的请求。客户端管不了这一点，但拆批能减少这种情况。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- 只改 `index/embed.py`，不改 `pipeline.py`、`cloud.py`、`core/`、`testing/`、工作流和模型。
- 测试全部离线，用 `tests/index/test_vector.py` 里现有的 `ollama` 假服务；可以扩展它的 script 能力，例如按请求里的条数决定是否 sleep。不新增依赖。仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **超时拆批**：`_embed_batch` 遇到**超时**（`httpx.TimeoutException`）时：
   - 批里多于 1 条：不原样重试，立刻把这一批对半拆开，分别嵌入，按原顺序拼回结果。每一半同样适用本规则，可以递归，最小拆到 1 条。
   - 批里只有 1 条：沿用现有行为，按 `retries` 原样重试（含 backoff），用尽后抛 `EmbeddingError`。
2. **其他错误不变**：连接错误等非超时的 `TransportError` 和 5xx 仍按 `retries` 原样重试；4xx 立即失败；响应格式和维度校验不变。
3. **请求总数有上界**：一次 `embed()` 的 HTTP 请求数不超过 `(2 × 条数 − 1) × (retries + 1)`。在 summary 里给出推导，并用测试断言。
4. **错误信息**：`EmbeddingError` 的消息里不包含输入文本。现有的 `resp.text[:200]` 来自服务端响应，不是输入，保持不变；在 summary 里说明这一点。
5. **测试**（`tests/index/test_vector.py`，新增 `tests/regressions/test_audit_126.py`）：
   - 32 条的批次，假服务对 1 次以上多条请求 sleep 超时、对少条请求立即返回：结果 32 条，顺序与输入一致（假服务返回的向量要能区分每条输入，用来验证顺序），并且没有任何一次多条请求被原样重发。
   - 只有 1 条的超时：现有的 `test_ollama_timeout_is_retried`、`test_ollama_timeout_fails_after_retries` 保持通过，不改它们的断言。
   - 拆到单条后仍然超时：抛 `EmbeddingError`，请求数不超过要求 3 的上界。
   - 5xx、4xx、维度不符、连接拒绝的现有测试不变。
   - regression：输入文本里放假 URL、假路径、假密钥，各种失败情况下 `EmbeddingError` 的消息里都不出现这些字符串。
   - 其他已有测试和断言全部不变。
6. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不调整默认 `timeout`、`batch_size`、`retries`，不换模型，不截断文本。提速另行讨论。
- 不改补嵌和重试计数的规则（124、125）。
- 不做真实联网测试。真实效果由主对话在合并后再跑一次 `cloud-embed` 来验证。
