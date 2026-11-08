---
id: DATUMOJ-MAPLE-124
title: 向量化单篇出错改为待补重试（达到上限才算来源失败）；cloud run 用剩余时间补嵌积压
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-123"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/pipeline.py", "src/datumoj/rikolto/cloud.py", "tests/test_pipeline_vectors.py", "tests/test_pipeline_retry.py", "tests/test_pipeline_publish.py", "tests/test_cloud_run.py", "tests/regressions/test_audit_124.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-124/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/**", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_pipeline_vectors.py
    - cmd: make check T=tests/test_pipeline_retry.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-124/summary.md 完成说明"]
  review: {by: other_family, checklist: ["单篇向量化出错是否进入待补、下一轮自动重试，而不是永远留在 missing 里", "同一篇连续失败达到上限后是否才记 embedding_failed，且不会无限重试", "每轮嵌入总数是否仍受 max_embed 限制（补嵌也计入）", "补嵌是否只在来源都处理完、剩余时间够时进行，并在整轮预算到点前停下", "向量状态文件的旧格式是否仍能读（没有重试计数的记录按 0 处理）", "进度行和结果 JSON 是否只新增计数字段，没有错误文本", "是否改了 core、索引实现或越界"]}
  human: sampled
max_attempts: 5
---
# 目标
向量化里单篇偶发出错，不再让整个来源记成失败；它会进入待补，下一轮自动重试。积压的待嵌入文本块，可以用每轮剩下的时间和嵌入额度逐步补上，不必等那个来源下次再被抓取。

# 背景
2026-11-01 第 2 轮 Cloud run（第 2 代）：
- jp-egov-law 新增 739 个快照，嵌入了 696 个文本块，积压 25,416 个。积压大部分是 `vector_limit`（每轮 `--max-embed 1000` 的有意延后），共 303 篇。
- 另有 **1 篇**是 `EmbeddingError`。`cloud._source` 把非 `vector_limit` 的错误都算成 `embedding_failed`，于是整个来源记为失败，整轮退出码为 1。
- `pipeline._retry_vectors` 只重试 `error == "vector_limit"` 的记录，所以出过 `EmbeddingError` 的那篇**永远不会再试**。
- 补嵌只在 `parse_pending(deps, source_id)` 开头、针对同一个来源做。积压的来源要等到下次被调度时才能补；而且每轮常常因为时间预算，后面的来源根本轮不到。

嵌入服务放在哪里跑（Actions 的 CPU 还是别处），由人另行决定，不在本任务里。本任务只改失败的判定和补嵌的时机。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- `core/` 和 `testing/` 不改，`index/`（嵌入器和向量库实现）不改。
- 先读 `pipeline.py` 的 `_load_vector_state`、`_mark_vector_missing`、`_clear_vector_missing`、`_retry_vectors`，以及向量状态的校验，再决定计数存在哪里。
- 结果 JSON 的现有字段和含义不变，只允许新增字段。
- 测试全部离线，用现有的 HashEmbedder 或假向量库。不新增依赖。仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **重试**：向量状态里每条 missing 记录多记一个连续失败次数，例如 `failures`。
   - 非 `vector_limit` 的错误每出现一次，计数加 1；成功后清除整条记录（现有行为）。
   - 旧格式没有这个字段，按 0 读入，不能因此失败关闭。
   - `_retry_vectors` 同时重试 `vector_limit` 记录，以及计数小于上限（常量 3）的出错记录。达到上限的记录不再自动重试，但要保留，以便以后人工处理。
2. **失败判定**（`cloud._source`）：只有本轮让某篇的计数**达到上限**时，才计入 `embedding_failed`。没达到上限的出错，计入新字段 `embedding_deferred`，不让来源失败。进度行的 `failures` 只放 `embedding_failed`；`embedding_deferred` 作为单独的计数键加进进度行的白名单。
3. **补嵌积压**（`cloud.run`）：
   - 到期的来源都处理完（或时间预算用完）之后，如果剩余时间还不少于 2 分钟、嵌入额度 `remaining` 还大于 0，就对所有来源补嵌积压，沿用 pipeline 现有的补嵌入口，不重新解析。
   - 补嵌计入同一个 `max_embed` 额度。
   - 补嵌不能越过整轮预算：按批进行，每批之间检查剩余时间。批大小由实现定，写进 summary。
   - 结果 JSON 新增 `backfill_embedded`（补嵌成功的文本块数）和 `vector_pending`（补完后剩下的积压数）；进度行新增 `backfill_start` / `backfill_end` 两个事件。
   - 补嵌出错按要求 1、2 处理，不影响已发布的状态。
4. **测试**：
   - `tests/test_pipeline_vectors.py` 或 `tests/test_pipeline_retry.py`：
     - 一篇第 1 次出错后，下一轮会重试，成功后记录清除；
     - 连续 3 次出错后不再重试，记录保留；
     - 旧格式（没有计数）的状态文件能读。
   - `tests/test_cloud_run.py`：
     - 单篇出错 1 次时，来源不算失败，`embedding_deferred == 1`；
     - 达到上限的那一轮，来源才失败；
     - 有积压、时间够时会补嵌，并且补嵌计入 `max_embed`；
     - 时间不够时不补嵌。
   - 新增 `tests/regressions/test_audit_124.py`：
     - 让嵌入器抛出含假 URL、假路径、假密钥的异常，断言 stderr、结果 JSON 和向量状态文件里都不出现这些字符串（现有的 `safe_reason` 应该已经保证）；
     - 进度行的键是白名单。
   - `tests/test_pipeline_publish.py` 的 `test_faults_and_next_run_recovery` 第 168 行（`point == "vectors"` 时断言 `reindex(restarted, vectors_only=True) == 1`）：按要求 1，重启后的 `parse_pending(restarted)` 已经自动重试并补上那篇，所以 reindex 返回 0 是有意的新行为。只把这一处预期改成与新行为一致（例如断言重试已补上、reindex 为 0），同时保留后面"`restarted.vectors.search("modern")` 有结果"的断言。在 summary 里写明这是有意的改动和理由。
   - 其他已有的测试和断言全部不变。
5. `docs/deploy/runners.md` 补一段：嵌入额度、补嵌和重试上限的含义，以及 `vector_pending` 怎么看。
6. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 打回记录
- 第 1、2 次：全量验收失败在 `tests/test_pipeline_publish.py::test_faults_and_next_run_recovery[fake-vectors]` 和 `[sqlite-vectors]`（第 168 行 `assert 0 == 1`）。原因是要求 1 的自动重试让 reindex 没有可补的了；这个文件当时不在 scope 里，现在已加入，按要求 4 改。

# 不做
- 不改 `core/`、`index/`、嵌入模型和工作流。不决定嵌入在哪里跑。
- 不改发布和拉取（DATUMOJ-MAPLE-123 已做）。
- 不做真实联网测试，不碰任何真实凭据。
