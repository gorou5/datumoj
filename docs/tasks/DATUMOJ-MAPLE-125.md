---
id: DATUMOJ-MAPLE-125
title: 单独的云端嵌入作业：rikolto cloud embed 只补嵌积压并发布代次，新增 cloud-embed 工作流（手动触发）
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-124"]
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/cli.py", "src/datumoj/rikolto/pipeline.py", ".github/workflows/cloud-embed.yml", "tests/test_cloud_embed.py", "tests/test_cloud_workflow.py", "tests/regressions/test_audit_125.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-125/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/workflows/cloud-run.yml", ".github/workflows/ci.yml", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/**", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/test_cloud_embed.py
    - cmd: make check T=tests/test_cloud_workflow.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-125/summary.md 完成说明"]
  review: {by: other_family, checklist: ["cloud embed 是否只补嵌、不抓取不解析，且和 cloud run 用同一套配置检查、存储探测、写入租约、还原、发布和释放流程（复用而不是复制一份）", "两个工作流是否在同一个 concurrency group，且持租约互斥，不会同时写", "嵌入是否按批进行、每批之间检查剩余时间，到点前停下并照常发布", "没有积压时是否不发布新代次（不产生空代次）", "嵌入出错是否沿用 124 的重试和上限规则", "新工作流的 secrets 是否只经 env 引用、权限只读、只能在 gorou5/datumoj 上跑、没有 pull_request 触发", "结果 JSON 和进度行是否只有计数、阶段名和秒数", "cloud run 的行为和结果字段是否不变", "是否改了 core、索引实现或越界"]}
  human: sampled
max_attempts: 4
---
# 目标
新增 `rikolto cloud embed`，再配一个手动触发的 GitHub Actions 工作流 `cloud-embed.yml`。它在 Actions 上专门补嵌向量积压，一次跑 1 到 2 小时，然后发布新代次，不占用抓取轮次的时间。

# 背景
2026-11-02 第 3 轮 Cloud run（第 3 代）：
- 嵌入了 1000 个文本块，正好是 `--max-embed 1000` 的上限。积压从 25,416 增加到 **60,410**。
- 按第 2、3 轮 jp-egov-law 的数字粗算：Actions CPU 上 ollama bge-m3 每个文本块约 0.13 秒（约每秒 8 个），解析每个快照约 1.9 秒。嵌入本身不慢，慢在每轮只给 1000 个额度，而且和抓取挤在同一个 40 分钟预算里。
- 人决定：嵌入继续放在 Actions，单独开一个作业。

依赖 DATUMOJ-MAPLE-124，它已经提供：
- 嵌入出错的重试和上限；
- "到期来源处理完后补嵌积压"的入口；
- 结果字段 `backfill_embedded`、`vector_pending`。

本任务复用这些，不另写一套。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- `core/`、`testing/`、`index/` 不改。
- `pipeline.py` 只在 124 的补嵌入口不够用时做最小改动，并在 summary 里说明原因。
- `.github/workflows/cloud-run.yml` 不改。
- 测试全部离线，用现有 `tests/test_cloud_run.py` 的 `world` 夹具、假时钟和 HashEmbedder。不新增依赖。仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **`cloud.embed(*, work_dir=None, max_minutes: float, reserve_minutes: float = 10, max_embed: int | None = None) -> dict`**：
   - 和 `cloud.run` 共用同一套流程：配置检查、存储探测、写入租约（取得、续期、到期接管、释放）、还原（含孤儿候选续传）、发布、`_quiet()`、进度行，以及租约释放失败时判为失败。**把共用部分提出来复用，不要复制一份**。`run` 的行为、参数和结果字段一律不变。
   - 中间一步换成：**不抓取、不解析**，只对所有来源补嵌积压。按批进行（批大小沿用 124 的，或在 summary 里说明），每批之前检查剩余时间，不足 1 分钟就停。`max_embed` 不为 None 时，总数也不超过它。
   - 有补嵌成功时才发布新代次。一个都没嵌成功（没有积压，或全部出错），就**不发布**，释放租约后返回，`generation` 为 None。
   - 出错的处理沿用 124 的重试和上限规则。达到上限的条数计入 `embedding_failed`，此时 status 为 failed、exit_status 为 1。其余情况 status 为 completed、exit_status 为 0。
   - 结果 JSON 至少包含：`status`、`exit_status`、`generation`、`embedded`、`vector_pending`（补完后的积压数）、`embedding_deferred`、`embedding_failed`、`budget_exhausted`、`reason`、`publish_timings`、`restore_timings`。
   - 进度行沿用 `run` 的白名单写法。事件为 `embed_start`、`embed_batch`（每批一行，只含累计数和耗时）、`publish_start`、`publish_end`、`round_end`。
2. **CLI**：`rikolto cloud embed --max-minutes N --reserve-minutes R [--max-embed M]`，写法照 `cloud run`，结束时打印结果 JSON，退出码等于 `exit_status`。
3. **工作流 `.github/workflows/cloud-embed.yml`**：
   - 以 `cloud-run.yml` 为模板。只有 `workflow_dispatch`（先不加定时）。`concurrency` 与 cloud-run **同一个 group**（`rikolto-cloud-run`），`cancel-in-progress: false`。
   - 权限 `contents: read`，`if: github.repository == 'gorou5/datumoj'`。
   - ollama 安装（含 SHA256 校验）、模型缓存、依赖安装照抄 cloud-run 的写法。
   - secrets 只经 env 引用，不需要 `ESTAT_APP_ID`。
   - 运行 `uv run --frozen rikolto cloud embed --max-minutes 100 --reserve-minutes 15`，`timeout-minutes: 150`。
4. **测试**：
   - 新增 `tests/test_cloud_embed.py`：
     - 有积压时会补嵌并发布，`vector_pending` 减少；
     - 没有积压时不发布、租约已释放；
     - 假时钟下时间到点会在批间停下并照常发布，`budget_exhausted` 为真；
     - `max_embed` 限制总数；
     - 达到重试上限时 status 为 failed；
     - 租约被占时跳过（与 `run` 一致）；
     - 不调用任何爬虫或解析器（用会抛错的替身断言）；
     - CLI 把参数传到 `embed()`，退出码正确。
   - `tests/test_cloud_workflow.py`：对 `cloud-embed.yml` 做同样的静态检查，并断言两个工作流的 concurrency group 相同。可以把现有检查参数化，但 cloud-run 那份检查的断言不能放松。
   - 新增 `tests/regressions/test_audit_125.py`：
     - 让嵌入器抛出含假 URL、假路径、假密钥的异常，断言 stderr 和结果 JSON 里都不出现；
     - 进度行的键是白名单；
     - 同一个数据目录上 `run` 和 `embed` 不能同时持有写入租约。
   - 已有的测试和断言全部不变。
5. `docs/deploy/runners.md` 补一段：两个工作流的分工、共用租约和并发组、建议的触发方式（抓取后手动触发嵌入；稳定后再加定时）。
6. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不加定时触发（稳定后由主对话另提）。
- 不改 `cloud-run.yml`、`core/`、`index/`、嵌入模型。
- 不改"发布后完整还原核对"每次都下载全部 raw 的做法，这个由主对话另行评估。
- 不做真实联网测试，不碰任何真实凭据。真实效果由主对话在合并后手动触发工作流来验证。
