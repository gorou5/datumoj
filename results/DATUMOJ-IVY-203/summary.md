# DATUMOJ-IVY-203 完成说明

实现 JMA `regular_l.xml` 长期 feed 采集和防灾 XML 解析。采集器每次抓取并保留 feed 快照，用 Atom `updated` 与已成功保存的 entry id 判断增量；不依赖 feed id、entry 数量或文件大小，也不跟随 feed 的 `rel="self"` 链接。entry URL 限定为来源配置允许的 HTTPS host。失败的 entry 会在后续轮询重试。

解析器安全解析 XML，拒绝 DTD/实体声明；Atom feed 快照不生成文档。每个 JMA Report 生成一份 Document 和一个正文 Chunk，带标题、发表日期、官署、对象地区、原始发表时间及 `entry=<id>` locator。VPFG50、VPWP50 的可选字段和空元素不会阻断解析。来源配置设为每小时轮询 `regular_l.xml`。

## 验收

- 命令：`make check T=tests/crawlers/test_jma_feed.py`
- 结果：15 passed；日志见 [test-output.log](test-output.log)。
- 测试只使用录制 fixture 和 `FakeHttpFetcher`，没有联网。
- 继承并通过 `CrawlerSuite`、`ParserSuite`；另覆盖固定窗口两快照等大小、等条数但新增六个 entry，feed id 变化时已保存 entry 不重复抓取，可选字段/空元素、恶意 XML、feed 快照以及 YAML 来源配置。

## 审查核对

- 变更仅在任务 scope：采集器、解析器、来源配置、指定测试文件和本结果目录。
- 实现仅依赖 `core.contracts`、`core.models`、lxml 和标准库；测试用契约假实现及 `drive_crawler`。
- 测试全离线，没有修改契约、fixtures、依赖或其他开发线实现。

## 变更文件

- `src/datumoj/crawlers/jma_feed.py`
- `src/datumoj/parsers/jma_xml.py`
- `sources/jp-jma.yaml`
- `tests/crawlers/test_jma_feed.py`
- `results/DATUMOJ-IVY-203/summary.md`
- `results/DATUMOJ-IVY-203/test-output.log`

跳过实时推送和平年值；没有执行真实联网轮询或 launchd 调度。风险：每份报告目前是一个 Chunk，VPWP50 这类大消息可能需要后续按检索需求拆分。
