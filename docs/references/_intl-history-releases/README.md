# 国际历史发布参考包（_intl-history-releases）

| 项目 | 内容 |
|---|---|
| 提供者 | 37（jp-gov-data-api）团队 |
| 资料整理日期 | 2026-10-30 |
| 资料核对日期 | 2026-10-30 |
| 官方入口 | [Eurostat EDP 主页面](https://ec.europa.eu/eurostat/web/government-finance-statistics/excessive-deficit-procedure)；[EDP 通报表](https://ec.europa.eu/eurostat/web/government-finance-statistics/excessive-deficit-procedure/edp-notification-tables)；[Eurostat copyright notice](https://ec.europa.eu/eurostat/help/copyright-notice)；[IMF WEO 数据库页](https://www.imf.org/en/Publications/SPROLLS/world-economic-outlook-databases)（未捕获）。 |
| 需要 Key | 未核对；本包未说明是否需要 API Key，且未调用 API。 |
| 许可 / 利用规约 | [Eurostat copyright notice](https://ec.europa.eu/eurostat/help/copyright-notice)；其中 editorial content 的 CC BY 4.0 表述不说明关联存档文件的范围。；Eurostat copyright notice 对统计数据、元数据及出版物记有再利用条件；关联历史存档范围未说明。可否再分发：未核对。 |
| 本资料的可信度 | 仅读文档：Eurostat 页面捕获与衍生链接记录；IMF 页面未捕获；本包未调用 API。 |
| 对应的 DATUMOJ 任务 |  |
| 包标识 | _intl-history-releases 是按跨来源主题规则命名的目录名；维护者可将内容并入 Eurostat 与 IMF 参考包；本版仅为候选。 |
| 范围 | Eurostat EDP 通报页与 gov_10dd、gov_10a_main ESMS 页面；整理发布时点、可见发布标签、历史修订说明、单位与捕获边界。 |
| 证据边界 | notes.md 中的官方页面引文均来自 held/ 下相对文件行；衍生链接与维护者计划摘录另行标记，不当作官方措辞。 |
| 历史存档状态 | Eurostat previous notification tables 目标捕获结果为 HTTP 404；目标页未捕获，2009—2010 是否存在、历史范围及文件格式均为 NOT CHECKED。 |
| IMF 状态 | 三个 IMF WEO 页面捕获结果均为 HTTP 403；页面未捕获，历史版本可用性与条款均未知。 |
| 条款边界 | terms.md 仅记载 2026-10-30 Eurostat copyright notice 捕获中的短引文；关联存档材料是否在其范围内为 NOT STATED。条款分析请见 eu-eurostat 包，本包不重述。 |
| 作业限制 | 未调用 API，未下载任何数据文件；只持有 2026-10-30 这一批捕获；Eurostat 历史存档未捕获，IMF 页面未捕获。 |
| 草稿边界 | 随附 ref_draft 目录中没有可读草稿文件；本包没有以草稿作为证据。包名交叉引用遵循本任务给定的指示。 |
| 未决项 | 希腊 2009—2010 历次数据与 IMF WEO 旧版是否可得、档案格式、正式发布标识格式、指标键到具体数据项的映射及关联存档条款范围均未由现有捕获确定。 |

## 内容

- [notes.md](notes.md)：证据标签、发布时点、修订、单位、捕获结果与未决事项。
- [terms.md](terms.md)：Eurostat copyright notice 的捕获范围与关联存档范围状态。
- PLAN_CHECK_TABLE.md：维护者计划逐项核对的侧车文件，不随参考包提交。
