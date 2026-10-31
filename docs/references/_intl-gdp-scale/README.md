# WDI、IMF、OECD 名义 GDP 单位与数量级参考（_intl-gdp-scale）

| 项目 | 内容 |
|---|---|
| 提供者 | 37（jp-gov-data-api）团队 |
| 资料整理日期 | 2026-10-30 |
| 资料核对日期 | 2026-10-30 |
| 官方入口 | [WDI 元数据 glossary](https://databank.worldbank.org/metadataglossary/world-development-indicators/series/NY.GDP.MKTP.CN)；[WDI 指标页](https://data.worldbank.org/indicator/NY.GDP.MKTP.CD)；[IMF WEO](https://www.imf.org/en/Publications/WEO/weo-database/2025/October)；[OECD API 信息](https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html)（后两页未捕获）。 |
| 需要 Key | 未核对；本包未调用 API，现有资料未说明是否需要 Key。 |
| 许可 / 利用规约 | [CC BY-4.0](https://datacatalog.worldbank.org/public-licenses#cc-by)（S1）；WDI 指标页也显示 CC BY-4.0（S2）。；本包仅转录 WDI 页面条款标签；数据集条款见 intl-wdi 包，IMF 与 OECD 条款见 intl-imf 与 intl-oecd 包。可否再分发：未核对。 |
| 本资料的可信度 | 仅读文档：WDI 页面捕获（S1、S2）；本包未调用 API。 |
| 对应的 DATUMOJ 任务 |  |
| 包标识 | `_intl-gdp-scale` 是按跨来源主题规则命名的目录名；维护者可将内容并入 WDI、IMF 和 OECD 对应参考包。本版仅为候选。 |
| 调查范围 | WDI 的 NY.GDP.MKTP.CN 元数据 glossary 页面和 NY.GDP.MKTP.CD 指标页面已捕获；WDI NY.GDP.MKTP.CD glossary 请求转到站点错误页；IMF WEO 与 OECD 请求页未捕获。 |
| 用途 | 记录系列名称、页面单位措辞和倍率状态；未捕获的来源内容不作补写。 |
| 条款范围 | terms.md 转录两张 WDI 页面的条款标签，并指向既有来源包；本包不作许可判断或建议。 |
| 限制 | 未调用 API，未下载数据；页面捕获日期只有 2026-10-30。三个来源的相关元数据页面均有部分或全部未捕获的范围。 |
| 计划对照 | PLAN_CHECK_TABLE.md 是本次独立侧车，不属于参考包提交内容。 |

## 内容

- [notes.md](notes.md)：页面字段、来源标签、单位措辞、倍率状态和未决范围。
- [terms.md](terms.md)：两张 WDI 页面的条款标签及既有来源包指引。
- PLAN_CHECK_TABLE.md：逐项对照维护者计划摘录；作为侧车保存，不随参考包提交。

## 捕获边界

WDI 的 NY.GDP.MKTP.CN glossary 页面与 NY.GDP.MKTP.CD 指标页面各有一份捕获。WDI NY.GDP.MKTP.CD glossary 请求最终转至 `https://databank.worldbank.org/ddperror.aspx?msg=404`，最终响应为 HTTP 200 错误页，因此该元数据页记为 NOT CAPTURED。IMF WEO 页面 `https://www.imf.org/en/Publications/WEO/weo-database/2025/October` 与 OECD 页面 `https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html` 的捕获结果均为 HTTP 403，记为 NOT CAPTURED。捕获结果来自本地捕获记录，不是官方页面证据。

本包没有执行 API 请求或下载数据。捕获记录只有 2026-10-30 这一日；它不能说明其后的页面状态。`(Millions)` 是 WDI 指标页的显示标题；页面捕获没有说明下载值或 API 值是整美元还是百万单位，因此这些值的倍率为 NOT STATED。
