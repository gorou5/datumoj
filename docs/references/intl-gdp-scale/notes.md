# WDI 名义 GDP 单位与数量级资料笔记（docs_references_intl-gdp-scale）

## 证据标签

文件行号均相对于 held/ 中的捕获文件；不引用捕获文件第 1 行。每个直接引文来自一个正文行，并单独给出行号。S1、S2 是页面捕获；S3 是本地捕获记录，只用于描述页面能否取得，不是官方页面证据。M1 是维护者计划摘录，只用于核对其自身要求，不是官方来源。

| 标签 | 材料 | 用途 |
|---|---|---|
| S1 | WDI 元数据 glossary；`wdi_glossary_ny_gdp_mktp_cn.txt`；https://databank.worldbank.org/metadataglossary/world-development-indicators/series/NY.GDP.MKTP.CN | NY.GDP.MKTP.CN 的页面字段。 |
| S2 | WDI 指标页面；`wdi_indicator_ny_gdp_mktp_cd.txt`；https://data.worldbank.org/indicator/NY.GDP.MKTP.CD | NY.GDP.MKTP.CD 的标题、条款标签和显示值表头。 |
| S3 | 本地捕获记录；`capture_log.jsonl` | 捕获结果、HTTP 状态与最终地址；不是官方证据。 |
| M1 | 维护者计划摘录；`datumoj_plan_excerpt.md` | 维护者的来源选择与计算规则；不作为官方来源内容。 |

## 1. WDI：NY.GDP.MKTP.CN

| 页面字段 | 捕获文字 | 证据 |
|---|---|---|
| Indicator code | NY.GDP.MKTP.CN | S1；`wdi_glossary_ny_gdp_mktp_cn.txt:61` |
| Indicator name | 「GDP (current LCU)」 | S1；`wdi_glossary_ny_gdp_mktp_cn.txt:63` |
| Unit of measure | 「current LCU」 | S1；`wdi_glossary_ny_gdp_mktp_cn.txt:77` |
| Periodicity | 「Annual」 | S1；`wdi_glossary_ny_gdp_mktp_cn.txt:79` |
| Reference period | 「1960-2025」 | S1；`wdi_glossary_ny_gdp_mktp_cn.txt:81` |
| Dataset | 「WB_WDI」 | S1；`wdi_glossary_ny_gdp_mktp_cn.txt:75` |
| License Type | 「CC BY-4.0」 | S1；`wdi_glossary_ny_gdp_mktp_cn.txt:90` |
| License URL | 「https://datacatalog.worldbank.org/public-licenses#cc-by」 | S1；`wdi_glossary_ny_gdp_mktp_cn.txt:88` |

此页的单位文字为 current LCU。捕获文字未显示数据发布倍率；该项为 NOT STATED。

## 2. WDI：NY.GDP.MKTP.CD

| 页面字段 | 捕获文字与观察 | 证据 |
|---|---|---|
| 页面标题 | 「GDP (current US$)」 | S2；`wdi_indicator_ny_gdp_mktp_cd.txt:41` |
| License label | 「CC BY-4.0」 | S2；`wdi_indicator_ny_gdp_mktp_cd.txt:45` |
| 显示值表头 | 「Country」→「Most Recent Year」→「Most Recent Value」→「(Millions)」；捕获文本顺序把 `(Millions)` 放在 Most Recent Value 后，位于国家、年份、值这一显示表头块内。 | S2；`wdi_indicator_ny_gdp_mktp_cd.txt:66`；`wdi_indicator_ny_gdp_mktp_cd.txt:67`；`wdi_indicator_ny_gdp_mktp_cd.txt:68`；`wdi_indicator_ny_gdp_mktp_cd.txt:69` |

捕获文本保留了 `(Millions)` 与 Most Recent Value 显示块的相邻顺序，但没有说明该标题如何映射到下载文件或 API 返回值。下载值或 API 值是整美元还是百万单位：NOT STATED；不从显示标题推断数据倍率。

## 3. 各来源的单位与倍率状态

维护者样例将 IMF WEO、OECD、World Bank WDI 列入 #002 来源组；摘录原文为「IMF WEO、OECD、世界银行 WDI」（M1；`datumoj_plan_excerpt.md:4`）。此项只说明样例的来源选择。

| Source | Series checked | Unit wording found | Multiplier stated? |
|---|---|---|---|
| World Bank WDI | NY.GDP.MKTP.CN | 「current LCU」；S1，`wdi_glossary_ny_gdp_mktp_cn.txt:77` | NOT STATED；页面给出单位措辞，没有给出数值倍率。 |
| World Bank WDI | NY.GDP.MKTP.CD | 标题「GDP (current US$)」；Most Recent Value 表头后的「(Millions)」；S2，`wdi_indicator_ny_gdp_mktp_cd.txt:41`；`wdi_indicator_ny_gdp_mktp_cd.txt:68`；`wdi_indicator_ny_gdp_mktp_cd.txt:69` | NOT STATED；页面没有说明下载值或 API 值的倍率。 |
| IMF WEO | 样例所列 GDP 来源；具体系列页未捕获 | NOT CAPTURED；WEO 页面请求返回 HTTP 403。URL: https://www.imf.org/en/Publications/WEO/weo-database/2025/October | NOT CAPTURED |
| OECD | 样例所列 GDP 来源；具体系列页未捕获 | NOT CAPTURED；API 说明页请求返回 HTTP 403。URL: https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html | NOT CAPTURED |

样例规则写作：「各来源的发布倍率取自来源元数据」（M1；`datumoj_plan_excerpt.md:51`）。同一行还写作：「倍率未确认的来源不进下面的 spread」（M1；`datumoj_plan_excerpt.md:51`）及「记入缺口报告」（M1；`datumoj_plan_excerpt.md:51`）。这些是维护者样例自身的程序要求，不是供应方元数据结论。本包不计算 spread；IMF WEO 与 OECD 的倍率仍未确认，WDI 下载/API 倍率仍为 NOT STATED。

捕获记录显示，WDI NY.GDP.MKTP.CD glossary 请求转至 `https://databank.worldbank.org/ddperror.aspx?msg=404` 错误页，最终响应 HTTP 200；该 glossary 页面为 NOT CAPTURED。IMF WEO 与 OECD 页面请求均为 HTTP 403，均为 NOT CAPTURED。这里的 URL 与状态是本地捕获结果，不表示页面正文内容。

## 4. 范围限制

- 未调用 API，未下载数据，也未计算跨来源差异。
- 页面捕获只有 2026-10-30 一日；没有后续状态核验。
- WDI NY.GDP.MKTP.CD glossary 页面、IMF WEO 页面与 OECD 页面未捕获；具体倍率未知。
- WDI 的 NY.GDP.MKTP.CN 页提供单位文字但没有数值倍率；NY.GDP.MKTP.CD 页的 `(Millions)` 只记录在显示值表头块，下载/API 倍率为 NOT STATED。
- 本包没有把维护者计划文字转成官方数据事实。
