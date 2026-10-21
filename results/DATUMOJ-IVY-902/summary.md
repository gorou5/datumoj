# DATUMOJ-IVY-902 完成说明

开发与离线自动验证完成，等待人工验收；未提交 git commit、未打 tag。

## 改动与原因

- `src/datumoj/wiring.py`：在入口增加两处适配。CKAN 分页逐项委托现有 `CkanMetaParser`，保留单包字段校验、资源和警告；活动响应及资源正文不误送入目录解析器。目录多词查询逐词委托真实 SQLite 目录库，再做 RRF 合并，保留来源过滤和数量限制。未修改各开发线模块。
- `tests/integration/test_stats_e2e.py`：真实 CLI、注册表、采集器、解析器、raw/manifest、SQLite 目录/索引及 DuckDB/Parquet 的离线集成；新增适配器继承 ParserSuite / CatalogStoreSuite 的合格测试和错误输入、过滤检查。
- 四份 `sources/*.yaml` 已符合 schema，kind 分别对应 `ckan`、`stat_dashboard`、`estat_files`、`jma_feed`，无需改动。未改 CLI、契约、题库、受保护 fixture、依赖、配置或文档。

## 逐条核对要求

1. 原始 sources 注册表加载成功，四个 kind 与自动发现的真实采集器一致；快照 raw 哈希与解析成功记录逐条核对。
2. ReplayTransport → CLI crawl → parse → index --rebuild → sql 跑通。统计 Dashboard 全部 1057 条录制观测值均入库。人口 XLSX 三处 CLI 查询与原单元格一致：东京 2000 年 E24 = 12064、大阪 2010 年 O38 = 8865、冲绳 2015 年 T58 = 1434，单位均为千人，数据日期均为对应年份的 10 月 1 日。CSV 即使响应头声称 UTF-8，仍按实际 cp932 内容解析；L15 的 `-` 保留为 value=null、note="-"。
3. 原始 `catalog.yaml` 10 题，通过真实 `search_catalog` 查询，Recall@10 = **1.00 ≥ 0.85**。原始 `stats.yaml` 10 题由 evaluate 执行原 SQL，numeric_precision = **1.00**，逐题 CLI 查询复核。所有统计出处另以相同谓词、增加 `obs_id` 的 SQL 查询核对，URL、raw 哈希、locator、统计期间、许可范围和 cite 还原均通过；并直接读取原始 JSON 行或 CSV I977 核对数值。
4. JMA 真实采集器/解析器及存储完成三次轮询：首轮 3 个电文，第二轮只下载 2 个新发现电文，第三轮不下载电文；最终 5 个 entry 各存一次、5 块均能 cite 还原。完整两份录制 feed 另核对各 9383 条、重叠 9377 条、实际新增 6 条，详见下方回放边界。
5. 测试默认仅写 pytest 临时目录，模块导入不创建报告。设置 `DATUMOJ_EVIDENCE_DIR` 时才向指定目录写证据；本次显式指向 `/private/tmp/DATUMOJ-IVY-902-evidence` 验证该分支，再由任务执行过程复制精简证据至本目录。验收日志记录默认运行前后的受跟踪文件校验。

## 验证与证据

- 指定验收：`make check T=tests/integration/test_stats_e2e.py`，24 项通过。
- 回归：`make check T='tests/integration/test_stats_e2e.py tests/integration/test_law_e2e.py tests/test_cli.py tests/store/test_catalog.py'`，50 项通过。
- uv 在沙箱中不可用，Makefile 自动使用既有只读虚拟环境；未安装任何依赖、未联网。
- `validation.log`：验收、回归及工作区校验输出；`changed-files.txt`：变更文件清单。
- `catalog-evaluation.json`、`numeric-verification.json`、`population-verification.json`：逐题命中、数值和出处。
- `jma-verification.json`、`jma-full-feed-delta.json`：增量轮询与完整 feed 差分；`requests.json`：全部 crawl 请求；`corpus.json`：录制输入文件及 SHA-256。

## 回放边界与人工审核事项

- CKAN 只验证目录元数据：临时复制来源配置，将分页设为 5、资源格式白名单设为空；回放两页录制 package_search 和终止空页。录制的查询/排序/终止偏移映射到采集器实际请求，package 内容不变，不代表全站目录覆盖率。
- Dashboard 依据生产配置重新分批，合并录制元数据，原始 DATA_OBJ 记录和值均未改变；请求包络是临时合成数据。没有注入题库答案或用题库查询构建语料。
- JMA 实际新增的 6 个电文正文没有录制。为避免伪造正文，端到端发现 feed 从录制 feed 中投影：首轮保留 3 个有正文的条目，第二轮保留全部 5 个有正文的条目。这 2 个“新发现”条目在原始完整 feed 中本来已存在；完整原始 feed 的 6 条新增只验证了 ID 差分，未验证其正文采集和解析。投影发现 feed 明确标为 synthetic。
- stats 题库只 SELECT value，不返回 obs_id；通用评测框架另外计算的文档召回和数值出处出现率并非本检查点统计门槛。本测试未修改题库或框架，以原 SQL 断言数值正确率，并通过额外的带 obs_id CLI 查询逐题验证出处。目录召回使用目录接口，未将目录元数据伪装为文档块。
- 本次使用现有关键词检索模式，vectors=None；未运行 Ollama。没有执行真实抓取、定时任务、合并或版本 tag，这些仍由人工验收流程处理。

跳过：真实联网验收及未录制的 JMA 新增电文正文；未核对全站覆盖率或向量模式。风险：JMA 投影回放边界及入口适配逻辑需人工确认，之后才能合并并打 M2 tag。
