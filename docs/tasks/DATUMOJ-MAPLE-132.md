---
id: DATUMOJ-MAPLE-132
title: 云端运行真正增量：向量只追加、SQLite 改动局部化、恢复只下载缺少的块、发布后校验不再全量下载
project: DATUMOJ
status: done
priority: 1
depends_on: ["DATUMOJ-MAPLE-131"]
runner: claude
model: null
tier: high
resources: {mem_gb: 4, disk_gb: 2, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/state.py", "src/datumoj/rikolto/index/vector.py", "src/datumoj/rikolto/store/catalog.py", "src/datumoj/rikolto/store/chunks.py", "src/datumoj/rikolto/store/observations.py", "src/datumoj/rikolto/core/manifest.py", "src/datumoj/rikolto/pipeline.py", "src/datumoj/rikolto/cloud.py", ".github/workflows/cloud-run.yml", ".github/workflows/cloud-embed.yml", "tests/core/test_state.py", "tests/core/test_state_blocks.py", "tests/core/test_state_s3.py", "tests/core/test_state_vectors.py", "tests/core/test_manifest.py", "tests/index/**", "tests/store/**", "tests/test_cloud_run.py", "tests/test_cloud_embed.py", "tests/test_cloud_workflow.py", "tests/test_pipeline.py", "tests/test_pipeline_vectors.py", "tests/regressions/test_audit_122.py", "tests/regressions/test_audit_132.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-132/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "sources/**", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/runner.py", "src/datumoj/rikolto/core/schedule.py", "src/datumoj/rikolto/testing/**"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_cloud_workflow.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-132/summary.md 完成说明；每个库改前改后的新增块字节与新增数据量写进 docs/deploy/runners.md"]
  review: {by: other_family, checklist: ["一轮新增 N 篇时，上传和下载字节是否与 N 成比例、与库总大小无关", "向量是否只追加新文件，压实是否低频且仍能恢复；检索结果是否不变", "SQLite 的改动是否只改变化的行，查询结果和出处是否不变", "缓存命中时是否只下载缺少的块、逐块校验 sha256；缓存缺失或损坏时是否退回全量并成功", "发布后校验是否仍能发现缺失或损坏的对象和出处问题", "本机 pull、恢复、回滚是否不受影响", "工作流缓存是否不含密钥，fork 不执行", "是否越界"]}
  human: sampled
max_attempts: 5
---
# 目标
做到计划（`docs/plans/0.3-maple.md` 6.2 和第 7 节）约定的"只传增量"：一轮新增 N 篇时，上传和下载的量大致与 N 成比例，而不是与库的总大小成比例。

# 背景
2026-11-04 在 R2 上实测（整代约 700 MB）：

| 代次 | 实际新增的数据 | 新上传 | 主要是哪些库 |
|---|---|---|---|
| 9 | 补嵌 342 块（向量约 1.4 MB） | 108 MB | 全是向量 |
| 10 | 抓了几个来源 | 173 MB | 向量 106、目录 55（目录总共 62 MB） |
| 12 | 新增约 1,000 篇 | 116 MB | 文本块 101 |
| 13 | 新增约 1,000 篇 | 29 MB | manifest 15、目录 13 |

- 向量：`cloud.py` 发布前调用 `maintain_vectors`，`pipeline._maintain_vectors` 也会调用，都走 `index/vector.py` 的 `table.optimize()`，每轮压实重写数据文件。
- SQLite：按 256 KB 分块去重（`core/state.py` 的 `_BLOCK_SIZE`），但改动分散到各处时几乎每块都变。目录小改动就变了 55 MB，要定位是哪种写法（整表删了再插、重建等）。
- 下载：每轮开始整代下载约 700 MB；发布后为了校验（`post_publish`），又把整代副本加全部 raw 下载一遍，一轮 7.5 分钟且随数据增长。
- 目标是每小时跑一轮，这些不改，时间和 R2 用量都撑不住。

依赖 131（都改 `core/state.py`）。任务分支从 `feature/0.4` 拉出，合并回它。测试全部离线，不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. **先测量**：写一个离线可复现的测量（用夹具和假存储），输出每个库在"已有库 + 新增 N 篇"时的新增块字节。把改前改后的数字写进 summary。
2. **向量**：每轮只追加新的数据文件。压实改成低频：碎片数超过阈值，或距上次压实超过 7 天（常量写在模块里，记录上次压实时间）。检索结果不变；恢复和发布后校验不受影响。
3. **SQLite**：定位并修改造成大面积改写的写法，改成只改变化的行。如果 `store/catalog.py`、`store/chunks.py`、`core/manifest.py` 之外还有原因，在 summary 里列出来，不越界修改，由主对话决定。查询结果和出处不变。如果某个库即使局部改动也无法达到目标（例如 B 树页面本身分散），在 summary 里给出数字和原因。
4. **下载**：
   - 两个工作流用 `actions/cache` 保留上一轮工作目录里的副本块（以代次号作缓存键，允许回退到更早的缓存；固定 action 的提交哈希，沿用现有写法）。缓存里不能有密钥。
   - 恢复时只下载本地没有的块，逐块校验 sha256。缓存缺失或损坏时退回全量下载，并能成功。
   - 结果 JSON 的 `restore_timings` 增加下载字节和复用字节，只用白名单字段。
5. **发布后校验**：不再全量下载 raw 和副本。改为：用列表结果核对已上传对象的 key 和大小，上传时已校验过哈希；再抽样恢复出处（沿用 `_sample_provenance`）。保留 `rikolto state` 的完整校验命令供手动使用。
6. **测试**：
   - 新增 `tests/regressions/test_audit_132.py`：在假存储上做"大库 + 新增 N 篇"，统计上传和下载字节，断言与 N 成比例、与库的总大小无关（比如库大小加倍时字节基本不变）。
   - 向量：多轮追加不触发压实；到阈值或 7 天才压实；压实前后检索结果相同。
   - 恢复：缓存命中只下缺少的块；缓存缺失、损坏时退回全量且校验通过。
   - 发布后校验：对象缺失、大小不对时能发现；不再调用全量下载。
   - 工作流：缓存步骤的位置和键；`cloud-run.yml` 和 `cloud-embed.yml` 的步骤保持现有的一致性断言。
   - 已有的测试和断言全部不变；如果某条依赖"每轮压实"或"发布后全量下载"，在 summary 里列出并说明，只改这一点。
7. `docs/deploy/runners.md` 更新：增量的做法、缓存、压实频率、发布后校验的范围、完整校验怎么手动跑。
8. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。
9. **第 1 次审查已确认的要求（2026-11-05，重做时必须满足）**：
   - **压实必须真的低频**：解析路径按文档写向量（`pipeline._write_vector_document` → `vectors.add`，每篇至少一次 `table.add`），一轮 1,000 篇就有约 1,000 个碎片，128 的阈值每轮都会触发压实，而压实基本等于重写整库。要让普通轮次不触发压实：可以把一轮内的写入合并成少量 `add`，或者按真实写入频率定阈值，自己选最小的做法。
   - **测试走真实路径**：`tests/regressions/test_audit_132.py` 要有一条用真实 `LanceVectorIndex` 和 `FakeEmbedder` 接上 `parse_pending` 的用例：先建一个已有库，写入 200 篇单块文档，断言没有触发压实，且新增文件字节与 N 成比例、不接近向量库总大小。
   - **分库测量**：测量要放进 `tests/regressions/test_audit_132.py`（或其辅助函数），按库（向量、目录、文本块、manifest）分别输出"已有库 + 新增 N 篇"的新增块字节，并有断言。只有整体的 `uploaded`、`downloaded` 不算。
   - **改前改后的数字写进 `docs/deploy/runners.md`**：`results/` 被 `.gitignore` 忽略，审查看不到 summary，所以每个库的数字、某个库达不到目标的原因，都要写在这份文档里，提交进分支。对已有测试的改动也在这里列一行说明。
   - **工作流**：新增步骤不要直接用 `python`（工作流没有 `setup-python`），用 `uv run --frozen python`。
   - **容错**：`_maintenance.json` 损坏时不能让整轮失败，按"未知上次压实时间"处理。
10. **第 2 次审查和后续尝试确认的要求（2026-11-05，重做时必须满足）**：
   - **向量写入合并提交**：每次 `table.add` 都写一份列出全部碎片的版本清单，这些清单都会发布成副本，所以"每篇一次提交"会让每轮新增字节随碎片数增长（已有 1,000 碎片时每轮约 145 MB）。单靠调高压实阈值不够。要把解析路径一轮内的向量写入合并成少量提交（由 `pipeline.parse_pending` 或一个批量写入接口统一提交），同时保证中途失败或到 deadline 时，已提交的文档和待补嵌的记录仍然一致。批量写入能力不要用 `hasattr` 之类探测（`Mock` 的动态属性会误判），沿用协议或明确的类型判断。
   - **测试覆盖碎片数**：真实路径用例的已有库要按真实写法灌入（逐篇产生碎片，碎片数约等于文档数），再断言已有碎片数加倍时，一轮的新增字节基本不变。
   - **云端查询的观测代次**：`store/observations.py` 已加入 scope。`retain_generation` 现在要求全部 raw 都在本地，发布后不再全量下载 raw 后，云端工作目录保留不了代次证据，`tests/integration/test_maple_m1.py::test_two_cloud_generations_pull_mcp_and_operational_guards` 的 `query_sql` 对比会失败（前两次尝试的最后都卡在这里）。为已校验过的远端 raw 增加保留支持（只在 `verified=True` 这类已校验路径上放宽，本机 pull 的校验不放松），不引入全量下载，这条测试不改、要通过。
   - **不要等待回复**：调度器里没有人能回答问题。再遇到 scope 缺口，在 scope 内选最小的做法完成，并写进 `docs/deploy/runners.md` 的说明；确实做不到才结束，并把原因写清楚。

# 不做
- 不按来源拆库（ADR 0002 提过，这里不做，除非测量证明必须，那时在 summary 里提出）。
- 不压缩 raw，不改清理规则（131）。
- 不改爬虫和解析器。
- 不加定时触发（另做）。
