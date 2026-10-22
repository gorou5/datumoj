# DATUMOJ-TACHIBANA-903 完成说明

自动验收全部通过，85 题达到 PLAN 第 10 节及本任务的全部规定门槛。H4 人工验收仍待完成；本次没有注册 Claude Code、修改验收文档、提交、合并或打 tag。

## 修改文件与方案

| 文件 | 目的 |
| --- | --- |
| `src/datumoj/wiring.py` | 将已有 `change_log` 注入 `AnalysisTools`，与流水线共用持久化日志。 |
| `src/datumoj/cli.py` | CLI 评测注入工具层的 `list_changes`。 |
| `src/datumoj/eval/framework.py` | 新增 `kind: change`、事件期望、带时区的查询窗口、命中率、来源及语言分组指标和条件门槛。 |
| `eval/questions/changes.yaml` | 新增、修改、删除、抓取失败、零变化各一题；明确回放来源和合成修改边界。 |
| `tests/eval/test_framework_changes.py` | 验证分派、类型/来源/doc_key 前缀匹配、部分命中、多来源分组、零变化、异常及格式校验、报告和门槛兼容性。 |
| `tests/integration/test_tachibana_m3.py` | 两轮真实组件回放、周报、提醒、全部题库、CLI 门槛、真实 stdio MCP。 |
| `results/DATUMOJ-TACHIBANA-903/**` | 本说明、完整变更清单、验收输出与显式导出的证据。 |

复用已有日志、解析器、索引、存储、周报和监控实现；没有另造接口或改开发线模块。变化题调用 `list_changes`，用 `kind`、`source_id`、可选 `doc_key_prefix` 同时匹配，指标为期望事件的命中比例。失败事件允许没有 doc_key。空期望必须得到空列表和 `zero_hits`；未接入、异常或状态不一致都记失败。只有包含变化题的报告才自动增加 `change_hit_rate=1.0` 门槛，原有 IVY 门槛保持兼容。

本次使用已有关键词检索模式（vectors off），无需 Ollama 或外部模型。未修改受保护题库、fixture、契约、依赖、锁文件、Makefile、文档或其他 scope 外文件。

## 要求核对及证据

1. **变化题**：五题全部命中；题库指定固定查询窗口和来源，新增/删除使用具体文档键前缀。专项测试同时覆盖日志故障不能冒充零变化。
2. **两轮回放与周报**：2026-10-21、2026-10-22 两轮用 `ReplayTransport` 抓取政策网页、PDF 和 e-Stat 表格。IVY 语料复用已有 fixture 初始化。第二轮在临时副本修改政策正文、修改大阪 CSV 的一个数值、增加合成网页并把另一网页响应改为 HTTP 410。产生新增 1、修改 2、删除 1、抓取失败 1，共 5 个事件；再次解析不增加事件。周报逐来源核对总览、各事件节的条数与快照引用、无变化来源及提醒。HTTP 410 的失败事件没有 locator，报告如实注明不能生成位置引用；删除事件仍引用旧快照。
3. **监控**：临时运行历史明确人为构造三次连续失败，以及距固定时钟十天的 JMA 成功记录；`schedule status` 返回对应的 `consecutive_failures` 和 `retention_gap`，周报包含提醒和缺口来源。没有安装或启用本机调度服务。
4. **全部题库门槛**：IVY 60 题（law 20、stats 10、catalog 10、behavior 20），701 新题 20 题（gov_web 12、tachibana_gaps 8），变化题 5 题，全部载入评测。各题库单独复核适用的规定指标，防止总体平均掩盖题库退化。
5. **MCP**：真实 Python 子进程经生产 CLI 启动 stdio 服务器，使用默认 wiring 工厂读取临时数据目录。`list_changes` 按来源、窗口和 kinds 返回新增/修改/删除三条政策事件，均通过 `cite` 还原；`search_docs` 分别带 `html_page`、`pdf` kinds，核对每个命中文档类型并还原引用。父子进程均禁止政府 HTTP 联网。
6. **写入边界**：数据、响应修改副本、调度历史和 CLI 评测报告均在 pytest 临时目录。证据默认也在临时目录；仅设置 `DATUMOJ_EVIDENCE_DIR` 才持久导出。本目录 m3 证据来自显式设置该变量的专项验收；全量验收不设置该变量。最终 git 差异确认没有受保护文件或 scope 外变更。

小站点 fixture 的正文短于正式解析器要求的 100 字；测试只在临时合成副本补充说明文字，原 fixture 保持只读。统计副本的 392940 → 392941、政策修改及 HTTP 410 都是人工测试情景，不能当作真实政府数据变化。

| 规定指标 | 门槛 | 实测 |
| --- | ---: | ---: |
| Recall@10 | ≥ 0.85 | 0.9857142857 |
| SQL 数值精确匹配 | 1.00 | 1.00 |
| 数值出现率 | 1.00 | 1.00 |
| 引用可还原率 | ≥ 0.95 | 1.00 |
| 缺口正确拒答率 | 1.00 | 1.00 |
| 变化题命中率 | 1.00 | 1.00 |

统计题库 Recall 为 0.90，其余有检索指标的题库均为 1.00。报告保留两项单题诊断：`stat-estat-tokyo-ict-employees-2016` 的预期文档未进入前十，但 SQL 数值和引用校验通过；`behavior-time-estat-period-publication` 未取得期间或发布日期，时间语义指标为 0.8571428571，未被列为现行硬门槛。按来源分组时 e-Stat 的 Recall 为 0.80；PLAN 没有另设逐来源门槛。本任务没有改题、降阈值或越界修改检索/解析实现。这两项诊断也见 m2 既有完成说明。

证据入口：

- [完整评测报告](tachibana-m3/evaluation.md)、[逐题结果](tachibana-m3/evaluation.json)、[门槛结果](tachibana-m3/gate.json)
- [事件](tachibana-m3/events.json)、[请求](tachibana-m3/requests.json)、[抓取结果](tachibana-m3/crawls.json)、[语料清单](tachibana-m3/corpus.json)
- [周报](tachibana-m3/weekly.md)、[调度状态](tachibana-m3/schedule-status.json)、[stdio 调用证据](tachibana-m3/mcp-stdio.json)、[CLI 评测](tachibana-m3/cli-eval.json)
- [完整变更文件清单](changed-files.txt)

## 亲自运行的最终验收

| 原文命令 | 结果 | 输出日志 |
| --- | --- | --- |
| `make check T=tests/integration/test_tachibana_m3.py` | 3 passed | [acceptance-m3.log](acceptance-m3.log) |
| `make check T=tests/eval/test_framework_changes.py` | 23 passed | [acceptance-framework.log](acceptance-framework.log) |
| `make check T=tests` | 2080 passed，37 warnings | [acceptance-all.log](acceptance-all.log) |

`git diff --check` 通过。make 按仓库既有机制提示 uv 不可用并只读使用主仓库 `.venv`；未安装或升级依赖。全量告警为已有的多线程 fork 和 LanceDB `table_names()` 弃用提示，没有跳过或修改既有测试。

## 人工边界与残余风险

H4 需要人在 Claude Code 中注册 MCP，做真实问答，抽查引用支持率与断言级别，再写入 `docs/acceptance/v0.2.0-tachibana.md`。该文档不在 scope，任务也标记 `human: required`；本说明只证明自动化 stdio 工具调用及快照还原。合并并通过人工验收后，发布流程才可打 `v0.2.0-tachibana`。

没有访问真实政府接口、生产 R2、外部模型或付费 API。来源许可及 internal_only 标记沿用现有实现；本任务没有确认未知许可或扩大再分发授权。语料快照和测试版本不能证明当前线上站点仍保持同样格式。

跳过：H4 人工问答/引用支持率抽查、线上验证、验收文档及发布操作。
风险：保留上述两项单题诊断和 e-Stat 分来源检索缺口；离线通过不等于 H4 已完成。
