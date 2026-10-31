# 国际历史发布（_intl-history-releases）资料笔记

## 证据标签

引用只指向捕获正文行，不引用捕获文件第 1 行。每个直接引文均用「」括起、取自同一正文行，并保持在 20 个英文单词或 60 个日文字符以内。S6 是从通知页原始 HTML 机械提取的链接属性列表；页面可见链接文字已与正文核对，但 href 属于衍生记录，不是官方措辞。M1 是维护者计划摘录及样例，只用于说明待核对主张，不是官方证据。C1 是本次捕获状态记录，仅用于报告 HTTP 状态和时间，不是官方页面文字。

| 标签 | 材料 | 捕获日期与时间 | 用途 |
|---|---|---|---|
| S1 | Eurostat EDP 主页面；eurostat_edp_main.txt；https://ec.europa.eu/eurostat/web/government-finance-statistics/excessive-deficit-procedure | 2026-10-30 21:42 JST | 报告频次、可见发布日期链接及当前通知相关标题。 |
| S2 | Eurostat EDP 通报表页面；eurostat_edp_notification_tables.txt；https://ec.europa.eu/eurostat/web/government-finance-statistics/excessive-deficit-procedure/edp-notification-tables | 2026-10-30 21:42 JST | 发布月份、当前年预测说明、可见发布标题、历史表链接及存量流量调整说明。 |
| S3 | Eurostat ESMS gov_10dd；eurostat_esms_gov_10dd.txt；https://ec.europa.eu/eurostat/cache/metadata/en/gov_10dd_esms.htm | 2026-10-30 21:45 JST | EDP 覆盖年限、单位、发布日历及修订政策。 |
| S4 | Eurostat ESMS gov_10a_main；eurostat_esms_gov_10a_main.txt；https://ec.europa.eu/eurostat/cache/metadata/en/gov_10a_main_esms.htm | 2026-10-30 21:45 JST | gov_10a_main 单位及 B.9 与 EDP 表的元数据关系。 |
| S5 | Eurostat copyright notice；eurostat_copyright_notice.txt；https://ec.europa.eu/eurostat/help/copyright-notice | 2026-10-30 21:44 JST | 仅记载页面中可见的短引文；不判断关联存档文件的适用范围。 |
| S6 | S2 原始 HTML 的机械链接列表；eurostat_edp_notification_tables_links_derived.txt；基础页面 URL 同 S2 | 2026-10-30 21:42 JST | 仅核对页面可见链接文字与 href 目标；不是官方页面文字。 |
| M1 | datumoj_plan_excerpt.md | 随任务提供 | 维护者计划与请求样例，不是官方证据。 |
| C1 | 随任务提供的 capture_log.jsonl | 2026-10-30 | 本次捕获的 HTTP 状态与时间；不是官方页面文字。 |
本文件中的 `文件名:行号` 指向未随本包提交的本地保存记录（公开仓库不提交未核对再分发范围的原文）；读者核对时以各标签对应的官方 URL 为准。

## 1. 报告与发布时点

| 页面 | 捕获文字 | 证据 |
|---|---|---|
| EDP 主页面：成员国报告 | 「twice per year, at end-March and end-September.」 | S1；eurostat_edp_main.txt:193 |
| EDP 通报表页：成员国报告 | 「at the end of March and at the end of September.」 | S2；eurostat_edp_notification_tables.txt:186 |
| EDP 通报表页：Eurostat 发布 | 「In April and October of each year」 | S2；eurostat_edp_notification_tables.txt:188 |
| gov_10dd：报告期限 | 「before 1 April and 1 October」；同一行还限定前四个日历年及当年预测。 | S3；eurostat_esms_gov_10dd.txt:82 |
| gov_10dd：公布时间 | 「around three weeks after the transmission deadline」 | S3；eurostat_esms_gov_10dd.txt:199 |
| gov_10dd：EDP 统计发布 | 「published in Eurostat's news release and the Statistics Database twice per year (April and October).」 | S3；eurostat_esms_gov_10dd.txt:152 |
| gov_10dd：精确日期 | 「precise date of publication is presented in Eurostat's release calendar.」 | S3；eurostat_esms_gov_10dd.txt:152 |

这些页面的表述分别为 end-March/end-September、before 1 April/1 October，以及 April/October 发布、约三周后公布。逐页保留其原有措辞，不作调和或推算。页面还显示「Release calendar」链接（S1；eurostat_edp_main.txt:102；S2；eurostat_edp_notification_tables.txt:102）。衍生列表记录两个 href：/eurostat/news/release-calendar 与 /eurostat/publications/link-to-release-calendar（S6；eurostat_edp_notification_tables_links_derived.txt:2-3）。目标日历页不在本次捕获中。

## 2. 可见发布标签与历史表入口

| 页面显示 | 捕获文字 | 证据 |
|---|---|---|
| 通报表页标题 | 「October 2026」 | S2；eurostat_edp_notification_tables.txt:190 |
| 通报表页标题 | 「April 2026」 | S2；eurostat_edp_notification_tables.txt:220 |
| EDP 主页面的相关条目 | 「2nd notification (21 October 2026)」 | S1；eurostat_edp_main.txt:202 |

这些文字提供可见的月份或发布日期线索；捕获页面没有说明稳定、正式的发布标识格式：NOT STATED。这里不据此创造标识结构。

通报表页可见「Access previous notification tables」（S2；eurostat_edp_notification_tables.txt:250）。S6 的衍生 href 为 https://s-circabc.europa.eu/ui/group/ca7c9cc4-b473-4abc-8e95-263dcd57d79d/library/968f9c29-5c7c-4125-b8c4-537b297f46b6?p=1&n=10&sort=modified_DESC（eurostat_edp_notification_tables_links_derived.txt:8；仅为衍生链接记录）。捕获状态记录显示该目标于 2026-10-30 21:44 JST 返回 HTTP 404；目标页未捕获。档案向前覆盖多久、提供何种文件格式、是否含 2009—2010 通报均为 NOT CHECKED。

## 3. 历史 EDP 数据与修订

gov_10dd 说明多数成员国数据自 1995 年起可得，但部分国家序列较短；「The provision of historical EDP data」以及「on a voluntary basis」（S3；eurostat_esms_gov_10dd.txt:128）。页面中的历史 EDP 数据定义为最后四个参考年以外的数据。该元数据没有确认希腊 2009—2010 的具体存档或通知版本是否存在：NOT CHECKED。

每次 EDP 通报时，成员国可将先前提交数据修订回 t-4；更早年份则由 Eurostat 在成员国提供修订数据并经验证后更新数据库（S3；eurostat_esms_gov_10dd.txt:216；eurostat_esms_gov_10dd.txt:221）。对 April/October 实务，页面称「data reported before 1 April are preliminary」以及「revised annual data are notified before 1 October」；这些修订可能影响全部已报告年份（S3；eurostat_esms_gov_10dd.txt:224）。这说明捕获页面陈述的修订政策，不证明某一希腊数值或历史文件已取得。

## 4. 预测与存量流量调整

通报表页说当前年度数据「forecasts and not statistics」，并说明 Eurostat 的质量评估不覆盖这些预测（S2；eurostat_edp_notification_tables.txt:188）。维护者请求样例将预算目标或预测值、包括 IMF WEO 当年及以后年份预测，作为负例（M1；datumoj_plan_excerpt.md:37）。这里仅作文字类别对照：捕获的 Eurostat 说明与该样例的预测负例相符；不据此判断 IMF 页面内容或任何 WEO 版本。

通报表页说明存量流量调整是政府债务变化中、政府盈余或赤字之外的主要因素（S2；eurostat_edp_notification_tables.txt:189）。该说明支持样例中对存量与流量作概念对照的范围；本次没有捕获或比较希腊历史数值。

## 5. 单位与样例指标边界

gov_10dd 的 EDP 单位列为「millions of national currency」（S3；eurostat_esms_gov_10dd.txt:133）、「millions of euro/ECU」（S3；eurostat_esms_gov_10dd.txt:134）及「percentage of GDP.」（S3；eurostat_esms_gov_10dd.txt:135）。gov_10a_main 的单位描述为「millions of national currency units, millions of Euro and percentages of GDP.」（S4；eurostat_esms_gov_10a_main.txt:858；同页 881-882）。

元数据还称 ESA table 0200 的 B.9 「should correspond to the balance B.9 notified under the Excessive Deficit Procedure」并在「the gov_10dd_edpt1 table」中提供（S4；eurostat_esms_gov_10a_main.txt:500）。这只记录该行明确写出的 B.9 关系。

维护者样例列出的 debt_eur、deficit_eur、deficit_pct_gdp 是样例键（M1；datumoj_plan_excerpt.md:25; datumoj_plan_excerpt.md:38; datumoj_plan_excerpt.md:44）。ESMS 单位描述与这些键相关，但捕获内容没有逐键映射、精确字段映射或从 gov_10dd_edpt1 推导这些键的规则：NOT STATED / NOT CHECKED。样例写出的单位和代码不在此升级为官方字段对应关系。

## 6. IMF 页面捕获结果

以下是本次捕获状态记录，不是 IMF 官方页面内容；三个地址均返回 HTTP 403，页面内容未捕获：

| 页面地址 | 捕获时间（JST） | 结果 |
|---|---|---|
| https://www.imf.org/en/Publications/SPROLLS/world-economic-outlook-databases | 2026-10-30 21:43 | HTTP 403；NOT CAPTURED |
| https://www.imf.org/en/Publications/WEO/weo-database/2009/October | 2026-10-30 21:43 | HTTP 403；NOT CAPTURED |
| https://data.imf.org/en/datasets/IMF.RES:WEO | 2026-10-30 21:45 | HTTP 403；NOT CAPTURED |

因此本包不知道 WEO 历史版本的可用范围、2009—2010 覆盖或相关条款。较早的 IMF copyright page 捕获记录请见 intl-imf 包；本次未重新核查该记录。
