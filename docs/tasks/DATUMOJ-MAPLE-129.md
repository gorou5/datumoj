---
id: DATUMOJ-MAPLE-129
title: 云端单个来源不能拖垮整轮：单次下载有总时长上限、解析和嵌入受截止时间约束、来源内分阶段进度
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/http.py", "src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/pipeline.py", "tests/core/test_http.py", "tests/test_cloud_run.py", "tests/test_cloud_embed.py", "tests/test_pipeline.py", "tests/test_pipeline_vectors.py", "tests/test_pipeline_retry.py", "tests/test_pipeline_publish.py", "tests/regressions/test_audit_122.py", "tests/regressions/test_audit_129.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-129/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".github/**", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/runner.py", "src/datumoj/rikolto/core/schedule.py", "src/datumoj/rikolto/core/state.py", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_http.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_pipeline_vectors.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-129/summary.md 完成说明"]
  review: {by: other_family, checklist: ["慢速吐数据的响应是否在总时长到时中止、记为 network_error、不写 raw", "缺省时 HTTP 抓取器行为是否与改前完全相同（本机不受影响）", "解析到截止时间是否停在下一个快照之前、已写内容完整、剩余快照下一轮能继续", "嵌入是否在批次之间检查截止时间，没嵌的进入待补而不是丢失", "一个来源超时后，后面的来源是否仍会开始、整轮是否仍会发布", "新进度事件是否只含白名单字段，不输出 URL、正文、异常详情", "core 是否只改了 http.py，是否越界"]}
  human: sampled
max_attempts: 6
---
# 目标
云端一轮里，任何一个来源都不能把整轮拖到 Actions 时限被取消。到了截止时间，来源要停下、交出控制，本轮照常发布；卡在哪一步要能从日志看出来。

# 背景
2026-11-03 两轮 Cloud run 都被 90 分钟时限取消、没有发布：
- 41586651889：卡在 jp-jma 的嵌入（每篇一个超长文本块）。已由 #86 关掉 jp-jma 的嵌入处理，但暴露出"来源内的解析和嵌入不受截止时间约束"。
- 41657215542：卡在 jp-gov-web，`source_start` 之后 85 分钟没有任何进度。本机从空复现时抓取 15 分钟（deadline 停下）、解析 4.5 分钟都正常结束，所以问题只在云端出现。
- 现有缺口：
  - `core/http.py` 只有每次读取 30 秒的超时，没有单个请求的总时长上限，对方慢慢吐数据时一个大文件可以下载很久；`core/runner.py` 的截止检查只在两个文件之间做。
  - `cloud._source` 里抓取之后的 `parse_pending` 和嵌入不看截止时间。
  - 进度行只有 `source_start` / `source_end`，看不出卡在哪个阶段。
- 被取消的来源没有记录写回，下一轮仍算"从没跑过"、排第一，会一直卡在同一处。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。
- core 只允许改 `core/http.py`，其余 core（契约、runner、schedule、state）和 `testing/` 都不改。爬虫、解析器、索引实现、工作流、来源配置不改。
- 测试全部离线，用假时钟、假传输（httpx MockTransport 或现有夹具），不访问网络。不新增依赖。仓库是公开的，不写账号、密钥、本机绝对路径。

# 要求
1. **单次下载总时长上限**（`core/http.py`）：
   - HTTP 抓取器新增可选参数"单个请求的最长秒数"（含重试之外的单次尝试的整个下载过程，即连接、等响应、读完正文）。缺省为不限，本机行为与改前完全相同。
   - 超过时立即中止读取、关闭连接，结果记为 `network_error`，原因是脱敏的固定文字（不带 URL 参数里的 Key、不带正文）；不写 raw。
   - 时钟可注入，测试用假时钟验证，不真的等待。
2. **云端怎么设**（`cloud.py`）：给每个来源建抓取器时，把上限设成"该来源剩余的截止秒数"和 300 秒中较小的一个（300 写成模块常量）。不改 `core/runner.py`。
3. **解析和嵌入受截止时间约束**（`pipeline.py`、`cloud.py`）：
   - `parse_pending` 新增可选的截止时间参数（缺省不限，现有调用方行为不变）。到时间就停在下一个快照之前；已经开始的快照照常写完，写入的内容保持完整；没解析的快照留在待解析里，下一轮能继续。
   - 嵌入在批次之间检查同一个截止时间；到时间后，本该嵌入的文档按现有规则记为待补，不丢失、不记失败。
   - 云端传进去的截止时刻是"整轮预算（`--max-minutes`）结束时刻 + 宽限"，宽限写成模块常量 600 秒。**不是**抓取的来源截止时刻：抓取被截止后，这次抓到的快照仍要解析完（已有测试 `test_deadline_during_crawl_parses_publishes_and_retries_next_round` 依赖这一点，保持不变）。解析或嵌入因此停下时，来源记录的 `stop_reason` 为 `deadline`，沿用 127 的续跑排序。
   - 整轮在最后一个来源之后照常走 staged_recovery、补嵌（预算已尽则跳过）和发布。
4. **分阶段进度行**：在 `_source` 里新增 `crawl_end`、`parse_end` 两个事件，沿用现有 `_progress` 和白名单字段（计数、耗时、`stop_reason`、失败计数），不输出 URL、正文或异常详情。如果 `tests/regressions/test_audit_122.py` 的白名单断言因此要改，只加这两个事件名，其余断言不变，并在 summary 里说明。
5. **测试**：
   - `tests/core/test_http.py`：慢速吐数据的响应在总时长到时中止、记为 `network_error`、正文不返回；缺省不限时同样的响应能读完。
   - `tests/test_pipeline.py` 或 `tests/test_pipeline_vectors.py`：截止时间到了以后解析停下，剩余快照仍待解析，再跑一次能解析完；嵌入到截止时间后剩余进入待补。
   - `tests/test_cloud_run.py`：假时钟下一个来源在解析阶段超时，后面的来源仍会开始，整轮发布；进度行包含 `crawl_end`、`parse_end`。
   - 新增 `tests/regressions/test_audit_129.py`：复现"一个来源抓取后解析/嵌入很慢"的场景，断言整轮在"预算 + 宽限"内结束并发布，且该来源记为 `deadline`。
   - 已有的测试和断言全部不变（第 4 条说明的白名单除外）。
6. `docs/deploy/runners.md` 补两三句：单次下载的总时长上限、解析和嵌入也受截止时间约束、新增的两个进度事件。
7. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 重做说明（2026-11-04）
第 1、2 次尝试都只失败在 `tests/test_cloud_run.py::test_deadline_during_crawl_parses_publishes_and_retries_next_round`：原任务要求 3 把解析截止定成整轮预算结束，与"抓取被截止后仍解析本次快照"冲突。这是任务文件的错，已改为"整轮预算结束 + 600 秒宽限"。那条测试一个字不改，应当直接通过。从 `feature/0.4` 重新开始，只按本文件做。

# 不做
- 不改 `core/runner.py`、`core/schedule.py`、契约，不改到期规则和排序（127）。
- 不改爬虫、解析器、嵌入器、工作流和来源配置。
- 不加按文档的解析超时（子进程或信号），以后需要再另排。
