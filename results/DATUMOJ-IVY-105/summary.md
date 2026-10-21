# DATUMOJ-IVY-105 完成说明

已实现 `run_crawl(spec, crawler, deps, limit=None, dry_run=False) -> CrawlReport`。
当前 worktree 中没有上一轮的 runner / 测试文件，因此本次在允许的两个路径补齐实现，并覆盖打回意见中的复现场景。

## 需求逐条核对

| 要求 | 实现及验证 |
| --- | --- |
| 1. 通用执行入口 | `CrawlDeps` 注入契约类型的 raw、manifest、fetcher；`run_crawl` 只 import 标准库和契约 / 模型，无其他线的实现依赖。仓库内检索未发现已有的执行器调用方，无需修改 scope 外的导出或配置。 |
| 2. 注册表限速与上限 | 真实 YAML 注册表读取 `rate_per_sec`、`max_files`、`max_total_bytes`；执行器限制抓取间隔、抓取次数和累计正文大小。次数包含失败和 304；达到上限写“因上限停止”。103 客户端另按 spec 进行 host、robots、重试及流式单文件限制。测试覆盖文件上限、累计超限、恰好达到字节上限、单文件超限、限速和用时。 |
| 3. 条件请求、raw、manifest | 从 `last_success` 读取 etag / last_modified，即使最新记录失败也不使用失败记录的头。成功正文先存 raw，再写 snapshot；所有 HTTP 结果含失败和 304 均写 manifest。原文相同但返回 200 时计入未变化。UUID 防止重跑快照 ID 冲突。落库错误直接抛出，避免报告虚假成功。 |
| 4. 默认空正文 / SPA | 先调用 `Crawler.classify`；返回 None 才按内容检测。默认 N=100，HTML 去标签并排除 head / script / style / 注释，再结合 root、app、__next 特征判断。UTF-8 / BOM、UTF-16 BOM、cp932 正文判断不依赖扩展名、Content-Type 或 charset；原始字节不改写。空正文和 SPA 原文仍存 raw 以供诊断。测试覆盖脚本伪正文、Next、cp932 的 N 边界、短正常 HTML、JSON、二进制和插件覆盖。 |
| 5. 惰性计划和追加任务 | 初始计划边取边抓，优先处理 after_fetch 追加任务。插件在计划恢复时能读到前页落库结果；按去 Key 的 URL 去重。计划及回调总产出超过 10,000 个任务（包括重复 URL）记运行失败，阻止失控分页。同步异常和生成器迭代异常都写入报告。 |
| 6. 报告和 dry_run | 报告含 new、unchanged、按原因计数的 failures、stop_reason、elapsed_seconds；dry_run 只返回去重且 URL 去 Key 的初始计划，传递 limit，不抓取、不调回调、不写 raw / manifest。limit 由插件解释，不替代 spec.max_files。 |
| 7. 真实依赖的离线集成测试 | 使用 101 FileRawStore、102 SqliteManifest、103 PoliteHttpFetcher / ReplayTransport、104 load_registry 和假 Crawler。正常正文回放只读的 stat_dashboard 真实样本，已阅读其 README；边界样本写在 pytest 临时目录，未改 fixtures。测试全部离线并注入虚拟时钟。 |
| 8. 契约二对齐 | 使用 FetchTask / CrawlContext，`task.meta` 原样传入 `record_snapshot(snapshot, task.meta)`；先分类后默认判断；失败也执行 after_fetch；去重、条件请求、max_files 和失控分页均有测试。假采集器继承 CrawlerSuite，5 项合格测试包含在验收中。执行器本身没有对应的协议合格套件。 |

## 上次评审中的高置信度质疑

已处理累计字节超限时漏调 `after_fetch` 的问题。停止标记不会提前返回：先把当前结果记成 `too_large` 并落库，再完整迭代本次回调，最后停止；回调产出的任务不执行。

回归测试 `test_byte_limit_completes_callback_and_discards_followups` 使用 100 字节上限和两个 60 字节响应，断言回调依次看到 `ok` / `too_large`、第二条快照存在、超限正文不写 raw，且回调追加的第三个 URL 不被请求。另覆盖恰好达到 100 字节和 103 流式拒绝单个超大响应的情况。

## 验收与证据

- 按任务原文执行 `make check T=tests/core/test_runner.py`：**46 passed**，退出码 0，见 [acceptance.log](acceptance.log)。
- 101–104 对应的 RawStoreSuite、ManifestSuite、HttpFetcherSuite、RegistrySuite：**51 passed**，退出码 0，命令及输出见 [contracts.log](contracts.log)。
- make 正常使用已有的只读 .venv 回退；没有安装依赖或联网。
- 首轮测试有一条网络失败场景误将 robots 请求一并断开，实际得到符合 103 契约的 blocked_by_robots；修正临时 transport 的故障注入，使 robots 正常回放、仅正文请求断开后，验收全通过。
- 变更文件清单见 [changed-files.txt](changed-files.txt)；全部在 scope 内，未改契约、已有测试或 fixtures，未 git commit。

## 调用约定

调用方持有并关闭 raw / manifest / HTTP 依赖；例如将 `PoliteHttpFetcher.from_spec(spec)` 连同 raw 和 manifest 传给 `CrawlDeps`。执行器只依赖 HttpFetcher 协议，不自行创建、关闭或修改调用方的客户端。

## 跳过 / 未核对 / 风险

未运行真实联网抓取、其他开发线实现及全仓测试；本任务未提供或新增依赖，未修改 scope 外文件。
SPA 属于 100 字阈值的启发式判断，特殊页面由插件 classify 覆盖；dry_run 无正文，不能展开依赖响应才能发现的后续任务。累计超限在收到响应后判断，超限正文不落 raw。
