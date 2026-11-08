---
id: DATUMOJ-MAPLE-131
title: R2 清理：旧代次和无引用副本块 72 小时后清理，raw 按来源设保留期（默认 30 天、jp-jma 7 天），带开关
project: DATUMOJ
status: done
priority: 1
depends_on: []
runner: claude
model: null
tier: high
resources: {mem_gb: 2, disk_gb: 1, api: [], port: 0}
scope: ["src/datumoj/rikolto/core/contracts.py", "src/datumoj/rikolto/core/state_s3.py", "src/datumoj/rikolto/core/state_fs.py", "src/datumoj/rikolto/core/pull.py", "src/datumoj/rikolto/testing/fakes.py", "src/datumoj/rikolto/testing/contract_suites.py", "src/datumoj/rikolto/cli.py", "tests/core/test_contracts_state.py", "tests/core/test_state_fs.py", "tests/core/test_pull.py", "src/datumoj/rikolto/core/state.py", "src/datumoj/rikolto/core/manifest.py", "src/datumoj/rikolto/cloud.py", "src/datumoj/rikolto/pipeline.py", "src/datumoj/rikolto/analysis/tools.py", "sources/jp-jma.yaml", ".github/workflows/cloud-run.yml", ".github/workflows/cloud-embed.yml", "tests/core/test_state.py", "tests/core/test_state_blocks.py", "tests/core/test_state_s3.py", "tests/core/test_manifest.py", "tests/test_cloud_run.py", "tests/test_cloud_embed.py", "tests/test_cloud_workflow.py", "tests/test_pipeline.py", "tests/test_mcp_server.py", "tests/regressions/test_audit_122.py", "tests/regressions/test_audit_131.py", "docs/deploy/runners.md", "results/DATUMOJ-MAPLE-131/**"]
out_of_scope: [".orch/**", "CLAUDE.md", "AGENTS.md", "pyproject.toml", "uv.lock", "Makefile", "handoff/**", "plan/**", "docs/plans/**", "docs/tasks/**", ".env*", "catalog/**", "src/datumoj/rikolto/crawlers/**", "src/datumoj/rikolto/parsers/**", "src/datumoj/rikolto/index/**", "src/datumoj/eltiro/**"]
protected: ["tests/fixtures/**", "src/datumoj/rikolto/core/runner.py", "src/datumoj/rikolto/core/schedule.py", "src/datumoj/rikolto/core/rawstore.py", "src/datumoj/rikolto/core/rawstore_s3.py"]
acceptance:
  automated:
    - cmd: make check T=tests/core/test_state.py
    - cmd: make check T=tests/core/test_manifest.py
    - cmd: make check T=tests/test_cloud_run.py
    - cmd: make check T=tests/test_cloud_workflow.py
    - cmd: make check T=tests/regressions
    - cmd: make lint
    - cmd: make check T=tests
  evidence: ["测试输出日志", "变更文件清单", "results/DATUMOJ-MAPLE-131/summary.md 完成说明"]
  review: {by: other_family, checklist: ["当前代次和 72 小时内的代次是否永远可完整恢复，清理后逐一验证", "副本块是否只在不被任何保留代次引用时才删", "raw 是否先标记、晚删除，保留代次恢复时不会报缺 raw", "每个 URL 最新一份的保护是否生效，爬虫读上一版不会失败；jp-jma 的 entry 是否能正常过期", "待解析、staged 的快照的 raw 是否不会被删", "同一份 raw 被多个来源引用时是否取最长保留期、按最近一次抓取算年龄", "两个开关关闭时是否一个对象都不删，只报告；重新打开后超期的是否一并清掉", "清理失败是否不影响本轮结果和已发布的代次", "清理是否只在持有租约、发布和发布后校验成功之后进行", "结果 JSON 是否只含白名单字段，不含 key 或 URL", "是否越界改了爬虫、解析器、rawstore"]}
  human: sampled
max_attempts: 5
---
# 目标
R2 总量控制在 #84 定的 9 GB 上限内。旧代次和不再被引用的副本块 72 小时后自动清理；raw 按来源设保留期，jp-jma 7 天、其他默认 30 天。可以用开关临时停止，重新打开后，超期的数据一并清理。

# 背景
2026-11-04 实测和决定：
- R2 总量 3 GB 左右，一天翻倍。`copies/` 1.44 GB，其中只被旧代次引用的约 800 MB；代码里没有任何清理。每代新增 30–170 MB。
- jp-jma 的 raw 每天约 520 MB（本机实测，约 1,400 个文件），是最大的开销。
- NAS 每天用 `rclone copy` 拉整个桶存档，不会删除 NAS 上已有的文件（人已确认在拉）。所以 R2 上的 raw 不是唯一一份，可以过期。**raw 不能压缩**：NAS 以 sha256 文件名和原始字节存档。
- 仓库规则（#84）：总量超过 7 GB 后每次写入先报总量，8.5 GB 起冻结非必要新增，9 GB 全停。

已知的坑，实现时必须处理：
1. **爬虫读上一版 raw**：`crawlers/` 下 jma_feed、ckan、udata、data_europa、eurostat、fedreg、gov_web 都用 `ctx.raw.get(previous.sha256)`，删掉每个 URL 的最新一份会让抓取报错。
2. **jp-jma 的 entry 每条只有一个 URL**，如果无条件保护"每个 URL 的最新一份"，就一条都删不掉。
3. **恢复校验**：`core/state.py` 的 `_materialize` 在 manifest 引用的 raw 不在时报"远端缺少 manifest 引用的 raw"；`_sample_provenance` 要求 `snap.sha256 in refs`。保留的旧代次也要能恢复。
4. `pipeline.py` 解析时读 raw，`analysis/tools.py` 回答引用时读 raw，都可能碰到已删除的 raw。
5. `core/models.py` 的 `StateGeneration` 校验 `previous_generation` 必须紧接上一代，只校验编号，旧代次清单删掉后不要破坏这一点。

存储接口（这一节已由主对话确认，不是阻塞，直接按此实现）：
- `StateObjectStore.list()` 只返回键，`delete_if_match` 要 ETag。**新增**协议方法 `list_objects(prefix)`，返回每个对象的键、字节数、ETag（一个小的不可变数据类，放在 `core/contracts.py`）。`list()` 不变。
- S3 实现直接用 `list_objects_v2` 已有的 `Size` 和 `ETag`，注意返回的 ETag 要和 `get` 返回的格式一致，能原样传给 `delete_if_match`，并加契约测试。同时实现 `state_fs.py`、`testing/fakes.py`，在 `testing/contract_suites.py` 补契约用例。`core/pull.py` 和 `tests/core/test_pull.py` 里的存储包装，只有 `isinstance` 检查或测试需要时才补。
- raw 和副本块都在同一个状态存储的命名空间下（`raw/`、`copies/sha256/`），删除一律走 `delete_if_match`，不改 `rawstore`。总量和字节数都来自 `list_objects("")`，不逐个 HEAD，也不为了拿 ETag 下载对象。
- 无引用的副本块不需要创建时间：清理在持有租约时进行，这时没有别的写入在途，只看是否被保留代次引用。上一次中断留下的孤立块下一轮直接删，需要时会重新上传。
- `--no-cleanup` 加在 `cli.py` 的 `cloud run` 和 `cloud embed` 上。
- 以前几次尝试因为这两点停下、没写代码；现在范围已经放开。遇到别的缺口先选最小的实现继续，并写进 summary，不要空手结束。

本版本剩下的任务在 `feature/0.4` 分支上开发：任务分支从它拉出，合并回它。测试全部离线（假存储、假时钟），不新增依赖；不写账号、密钥、本机绝对路径。

# 要求
1. **副本和旧代次**（`core/state.py`）：
   - 保留：当前代次；创建时间在最近 72 小时内的代次。
   - 删除：其余代次的清单；`copies/sha256/` 下不被任何保留代次引用的块。
   - 永远不删：`current.json`、租约。
2. **raw 的保留期**：
   - 来源配置新增可选 `scope.raw_retention_days`（正整数，缺省 30，不合法时明确报错）。`sources/jp-jma.yaml` 设为 7。
   - 新增可选 `scope.raw_expire_latest_types`（字符串列表，缺省空）：快照 meta 的 `resource_type` 在列表里的，每个 URL 的最新一份也可以过期。`sources/jp-jma.yaml` 设为 `["entry"]`；feed 仍受保护。
   - 一份 raw 的年龄按引用它的所有快照里最近一次抓取时间算；保留期取引用它的来源里最长的。
   - 不过期的情况：每个 URL 最新的成功快照（除上一条的例外）；待解析或 staged 的快照；被任一来源的保护规则保护的。
3. **先标记、晚删除**：
   - manifest 新增一张表（增量迁移，不动已有表），记录"允许缺失"的 raw 及标记时间。
   - 年龄达到"保留期 − 3 天"就标记（jp-jma 第 4 天，保留期不足 3 天时立即标记），写进下一个发布的代次。
   - 只有当所有保留代次的 manifest 都已带上这个标记（即标记已超过 72 小时）、且年龄达到保留期，才真正删除。这样 72 小时内任一代次恢复都不会报缺 raw，raw 也不会提前被删。
   - `_raw_refs` 和恢复校验：标记过的 raw 不算必需，缺失不报错；`_sample_provenance` 接受标记过的快照。
4. **raw 缺失时的行为**：
   - 标记过且确实不存在：`parse_pending` 跳过，不记失败；`analysis/tools.py` 的引用照常给出元数据，并说明"原文已归档，云端不再保留"。
   - 没有标记却缺失：行为不变，仍然报错。
5. **开关**：
   - `R2_CLEANUP`（仓库变量，缺省开，`off` 为关）：总开关，管副本、旧代次和 raw。
   - `R2_RAW_CLEANUP`（仓库变量，缺省 `off`，`on` 为开）：只管真正删除 raw。标记不受它影响。
   - 工作流把两个变量传给命令（`cloud-run.yml`、`cloud-embed.yml` 都要）；命令行也支持 `--no-cleanup`。
   - 关闭时一个对象都不删，只在结果里报告可清理的量。重新打开后照同样规则执行，超期的一并清掉，不需要额外操作。
6. **时机和失败**：只在 `cloud run` 和 `cloud embed` 发布成功、发布后校验通过之后、仍持有租约时清理。删除前检查租约；清理中途失败只记录计数，不改变本轮结果和退出码，下一轮接着清。
7. **结果 JSON 新增 `cleanup`**，只用白名单字段：两个开关的状态；删除的代次数；删除的副本块数和字节；标记的 raw 数；删除的 raw 数和字节；可清理的量；R2 总量字节；`over_7gb` 布尔值。总量用已有的列表结果计算，不额外逐个 HEAD。进度行和 `test_audit_122` 的白名单如需调整，只加这一个键，在 summary 里说明。
8. **测试**：
   - 72 小时边界；当前代次超过 72 小时也保留；共用的块只要还有保留代次引用就不删。
   - 清理后，当前代次和每个保留代次都能完整恢复（`raw="remote"` 和 `raw="local"` 两种模式）。
   - raw：标记和删除的时机；每个 URL 最新一份受保护；jp-jma 的 entry 能过期而 feed 不能；多来源引用取最长保留期、最近一次抓取算年龄；待解析和 staged 不删。
   - 爬虫：jma_feed 在旧 entry 的 raw 被删后下一轮照常工作。
   - 开关：两个开关的各种组合；关闭时不删；重新打开后积压的一并清掉。
   - 清理中途失败不影响结果和发布；租约丢失时不再删除。
   - 新增 `tests/regressions/test_audit_131.py`：模拟多轮运行、时钟前进 10 天，断言 copies 不随代次无限增长，jp-jma 旧 entry 的 raw 被清掉，最新代次仍能完整恢复。
   - 已有的测试和断言全部不变。
9. `docs/deploy/runners.md` 补一段：清理规则、两个开关及其默认值、为什么 raw 不压缩、raw 过期后哪些功能受影响（不能从云端重新解析；NAS 上还有）。
10. `make lint` 0 条，`make check T=tests` 全部通过；全量测试要跑完，不要中途中断。

# 不做
- 不压缩 raw，不改 rawstore 和 NAS 的约定。
- 不改爬虫和解析器。
- 不做真正增量（132 做）。
- 不在任务里操作真实 R2，不改仓库变量。
