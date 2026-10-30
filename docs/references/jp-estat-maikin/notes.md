# 每月勤劳统计调查（jp-estat-maikin）资料笔记

## 证据标签

引用只指向捕获正文行；不引用捕获文件第 1 行。捕获 URL 和 CSV 响应大小来自捕获元数据，不是官方页面正文。M1 是维护者文字，不是官方来源。标题及文字依来源原样记录；本笔记不判断许可，也不选择系列。

| 标签 | 材料 | 用途 |
|---|---|---|
| S1 | e-Stat 调查页；estat_survey_00450071.txt；https://www.e-stat.go.jp/statistics/00450071 | 调查名、政府统计代码、全国调查简介。 |
| S2 | e-Stat 数据库列表；estat_db_zenkoku.txt | 全国调查数据库列表及其公开（更新）日期。 |
| S3 | e-Stat 原表 dbview；estat_dbview_0003138108.txt；https://www.e-stat.go.jp/dbview?sid=0003138108 | 旧原表标题、公开时间、调查月数量和首尾月份。 |
| S4 | e-Stat 全国调查文件树；estat_files_zenkoku.txt；https://www.e-stat.go.jp/stat-search/files?toukei=00450071&tstat=000001011791&page=1 | 文件树分组及树中显示的更新日期。 |
| S5 | e-Stat 最新结果表；estat_fl_latest_tables.txt | 最新结果表分组。 |
| S6 | e-Stat 实数原表；estat_fl_genpyo_monthly.txt | 月次实数原表分组。 |
| S7 | e-Stat 给付推计值列表；estat_fl_kyufu_suikei.txt | 给付推计值分组及所示时期。 |
| S8 | e-Stat 长期时系列表；estat_fl_longseries.txt | 月次长期时系列表的标题与结果表类型。 |
| S9 | e-Stat 当前累积表；estat_fl_cumulative.txt | 当前版实数、指数・伸び率、赏与列表。 |
| S10 | e-Stat 令和3年12月以前参考资料页；estat_fl_pre2021_note.txt | 基准时更新前资料的归档位置。 |
| S11 | e-Stat 従来版列表；estat_fl_juurai_longseries.txt、estat_fl_juurai_cumulative.txt | 従来版长期时系列表和累积表。 |
| S12 | e-Stat 公表値说明 PDF；estat_pdf_kouhyouchi.txt | 各类实数及推计值时期、指数说明。 |
| S13 | e-Stat 更新前资料说明 PDF；estat_pdf_pre2021_note.txt | 基准时更新、抽出调查系列和従来公表值说明。 |
| S14 | e-Stat 累积数据符号 PDF；estat_pdf_cumulative_symbols.txt | 产业、规模、就業形态和月份符号。 |
| S15 | 厚生劳动省首页及过去通知目录；mhlw_maikin_top.txt、mhlw_maikin_30-1i.txt | 首页所列通知与历史通知标题、日期。 |
| S16 | 厚生劳动省时系列推计通知；mhlw_maikin_30-1h.txt | 推计值说明与 e-Stat 发布位置。 |
| S17 | 厚生劳动省従来公表值订正通知；mhlw_maikin_30-1l.txt | 订正范围与页面所述影响。 |
| S18 | 厚生劳动省订正等通知；mhlw_maikin_30-1o.txt | 抽出调查系列、従来公表值及奖金数据订正。 |
| S19 | 厚生劳动省调查变更通知；mhlw_maikin_30-1p.txt | 调查系統和特别调查公表日期变更。 |
| S20 | 令和6年基准更新 PDF；estat_pdf_benchmark_r6.txt | 更新方法、指数追溯修订及伸び率处理。 |
| S21 | 2018年工资数据说明 PDF；mhlw_pdf_mikata_20180927.txt | 资料日期及 2004–2017 年值的后续修正说明。 |
| S22 | 厚生劳动省网站利用规约页；mhlw_chosakuken.txt；https://www.mhlw.go.jp/chosakuken/ | 厚生劳动省网站自己的条款文字。 |
| M1 | 维护者计划、配置和采集器摘录；datumoj_plan_excerpt.md | 仅用于与计划及维护者配置主张对照，不作官方证据。 |
| H1、H2 | CSV 头部捕获；estat_csvhead_cumulative_jissu.txt、estat_csvhead_juurai_jissu.txt | 当前版与従来版实数文件的捕获 URL 元数据、范围总字节数和列头。 |

## 1. 调查身份与范围

S1 页面标题为 毎月勤労統計調査，政府统计代码字段显示 00450071（S1；estat_survey_00450071.txt:2、500-504）。字段名的单行原文为「政府統計コード」（S1；estat_survey_00450071.txt:501）；字段值为「00450071」（S1；estat_survey_00450071.txt:502）。

全国调查按日本标准产业分类覆盖 16 个大产业中常用劳动者 5 人以上的事业所；页面称其从约 190 万个符合条件的事业所中抽取约 33,000 个，每月掌握工资、劳动时间和雇用变化，并列出现现金名目工资、实质工资及工时指标（S1；estat_survey_00450071.txt:504）。这是调查页的概述，不是所选表的字段代码。

## 2. 数据库列表与文件树的并列观察

| 页面 | 捕获到的观察 | 证据 |
|---|---|---|
| 数据库列表 | 全国调查的若干数据库分组行显示公开（更新）日 2016-03-31。该日期仅记录所捕获的数据库列表，不代表其他 e-Stat 入口的当前状态。 | S2；estat_db_zenkoku.txt:533-552 |
| 原表 dbview | 捕获到的旧表标题为 实数原表（2010年1月～），公开时间为 2016-03-31；页面的调查年月计数为 71/71，起止月份分别显示 2010年1月与 2015年11月。 | S3；estat_dbview_0003138108.txt:503-526、583-584、836、906 |
| 全国调查文件树 | 长期时系列表及实数・指数累积数据等分组可见 2026-10-23 更新；实数原表月次分组也显示该日期。 | S4；estat_files_zenkoku.txt:565-588 |

捕获到的 dbview 页面是 sid 0003138108 的一个原表，不能代替计划中的 API 表标识。没有检查 API 响应或统计 Dashboard 的数据内容；当前 statsDataId 是否存在以及如何按发布时点取得数据均为 NOT CHECKED。不要将数据库列表的日期解释为 e-Stat 没有当前文件或 API。

## 3. 公表值类型、时期与文件树位置

e-Stat 公表值 PDF 将实数分为多类。以下表格将 PDF 所述时期与文件树所见标签并列；文件树未明确标注一一对应关系的地方单独说明，不据此挑选试点系列。

| 公表值类型 | 来源文字所述时期 | 捕获到的 e-Stat 文件树位置 | 核对说明 |
|---|---|---|---|
| 每月的集计结果 | 平成15年12月以前；以及令和元年6月以后。PDF 原文写「毎月の集計結果を公表している。」（S12；estat_pdf_kouhyouchi.txt:10）。 | 长期时系列表；最新结果表；以及 实数原表（2012年1月～）月次分组（S4；estat_files_zenkoku.txt:565-588；S5；estat_fl_latest_tables.txt:503-530；S6；estat_fl_genpyo_monthly.txt:503-532）。 | 文件树展示这些分组，但没有把 2003年以前各月逐项映射到某个文件。 |
| 给付的推计值 | 平成16年1月—平成25年3月；PDF 说明该值用于雇用保险等追加给付相关的「給付のための推計値」（S12；estat_pdf_kouhyouchi.txt:15）。 | 实数原表・实数推计等 > 给付のための推計値（2004年1月～2013年3月）（S4；estat_files_zenkoku.txt:595-598；S7；estat_fl_kyufu_suikei.txt:503-540）。 | 文件树标题所示范围与 PDF 相符。 |
| 时系列比较的推计值 | PDF 的实数说明覆盖平成16年1月—平成23年12月；用途是制作可作时系列比较的指数（S12；estat_pdf_kouhyouchi.txt:11-21）。 | 实数原表・实数推计等 > 时系列比较のための推計値（2004年1月～2012年1月）；夏季赏与及年末赏与也列有 2004–2011 年推计值（S4；estat_files_zenkoku.txt:599-610、631-648）。 | PDF 期间终点是 2011年12月；实数文件树标题写到 2012年1月。保留两处原文差异，不合并成同一终点。 |
| 必要的复元后再集计值 | 平成24年1月—令和元年5月，PDF 称发布了必要复元后的再集计值（S12；estat_pdf_kouhyouchi.txt:22-24）。 | 文件树主分组为 实数原表・实数推计等；月次实数原表标题为 2012年1月起（S4；estat_files_zenkoku.txt:576-588；S6；estat_fl_genpyo_monthly.txt:503-532）。 | 捕获列表没有单独以 再集计值 命名一一对应的数据文件，具体文件对应未由此确认。 |
| 抽出调查系列 | 令和元年6月—令和3年3月的 500 人以上规模抽出调查系列作为参考值（S13；estat_pdf_pre2021_note.txt:36-41）。 | （参考）令和3年12月以前公表结果 > 【参考】500人以上规模抽出调查系列；其中列有长期时系列表和实数原表月次分组（S4；estat_files_zenkoku.txt:661-702）。 | 树显示 24 个月次文件，不将文件数当作月份数或时期证明。 |
| 従来的公表值 | 令和2年12月分以前；来源说明对早年数值采用旧的集计方法，并保留用于时系列比较的参考值（S13；estat_pdf_pre2021_note.txt:42-48）。 | （参考）令和3年12月以前公表结果 > 【参考】従来の公表值 > 长期时系列表及实数・指数累积数据（S4；estat_files_zenkoku.txt:709-730；S11；estat_fl_juurai_longseries.txt:503-546；estat_fl_juurai_cumulative.txt:530-563）。 | 従来版累积表显示调查年月 2020年12月；不同表的期间仍以各表标题为准。 |
| 令和4年1月基准时更新前的结果 | 更新前的令和3年12月以前数值作为参考值；令和4年1月更新将指数基准改为 2020 年平均=100（S13；estat_pdf_pre2021_note.txt:3-14）。 | （参考）令和3年12月以前に公表した结果等 > 留意事项；旧长期时系列表及累积数据另列于该参考分组（S4；estat_files_zenkoku.txt:661-686；S10；estat_fl_pre2021_note.txt:503-540）。 | 这说明的是更新前资料的归档标签，不表示维护者必须保留或使用某一系列。 |

## 4. 累积表、CSV 头部与筛选定义

当前文件树列有 实数・指数累积数据 的实数、指数・伸び率、赏与表（S9；estat_fl_cumulative.txt:543-556）。従来公表值文件树也列出相应三类表，调查年月分别显示至 2020年12月或 2020年9月（S11；estat_fl_juurai_cumulative.txt:551-563）。列表正文未显示这六个文件各自的下载查询参数；只有两份实数 CSV 头部捕获具有下载 URL 元数据。

| 版本 | 列表项 | 下载 URL、statInfId 与 fileKind | 完整资源字节数 | 观察到的 CSV 列头 |
|---|---|---|---:|---|
| 当前版 | 实数（S9；estat_fl_cumulative.txt:543-546） | https://www.e-stat.go.jp/stat-search/file-download?statInfId=000032189776&fileKind=1；URL 见 H1 捕获元数据。 | 20,971,360（Content-Range 总字节数；非完整下载） | 见 H1；estat_csvhead_cumulative_jissu.txt:2 |
| 当前版 | 指数・伸び率（S9；estat_fl_cumulative.txt:548-551） | NOT CAPTURED | NOT CAPTURED | NOT CAPTURED |
| 当前版 | 赏与（S9；estat_fl_cumulative.txt:553-556） | NOT CAPTURED | NOT CAPTURED | NOT CAPTURED |
| 従来版 | 实数（S11；estat_fl_juurai_cumulative.txt:551-554） | https://www.e-stat.go.jp/stat-search/file-download?statInfId=000031981250&fileKind=1；URL 见 H2 捕获元数据。 | 19,596,723（Content-Range 总字节数；非完整下载） | 见 H2；estat_csvhead_juurai_jissu.txt:2 |
| 従来版 | 指数・伸び率（S11；estat_fl_juurai_cumulative.txt:556-559） | NOT CAPTURED | NOT CAPTURED | NOT CAPTURED |
| 従来版 | 赏与（S11；estat_fl_juurai_cumulative.txt:562-565） | NOT CAPTURED | NOT CAPTURED | NOT CAPTURED |

两份已捕获列头的日文原文如下（无引号转录）：種別, 年, 月, 産業分類, 規模, 就業形態, 現金給与総額, きまって支給する給与, 所定内給与, 所定外給与, 特別給与, 総実労働時間, 所定内労働時間, 所定外労働時間, 出勤日数, 前月末労働者数, 増加労働者数, 減少労働者数, 本月末労働者数, パートタイム労働者数（H1；estat_csvhead_cumulative_jissu.txt:2；H2；estat_csvhead_juurai_jissu.txt:2）。上列中文说明不替代此处的日文列头。

响应范围是各 12,288 字节的 CSV 头部；完整资源总字节数来自响应 Content-Range 元数据。没有取得完整 CSV 或 Excel 文件。两份头部开头的记录为 1960 年、月份代码 CY；这不是完整文件的月份覆盖证据，且没有捕获到月次行存在与否（H1；estat_csvhead_cumulative_jissu.txt:3-6；H2；estat_csvhead_juurai_jissu.txt:3-6）。

计划筛选维度与捕获定义的对应如下。这里只记录可见名称和符号，不补 API 查询代码。

| 计划筛选项 | 捕获定义 | 证据与边界 |
|---|---|---|
| 全国 | S1 标为全国调查；累积符号 PDF 未定义地域代码。 | S1；estat_survey_00450071.txt:504；S14；estat_pdf_cumulative_symbols.txt:3-219。全国调查身份不等于捕获到地域代码。 |
| 调查产业计 | 符号 TL 对应调查产业计。 | S14；estat_pdf_cumulative_symbols.txt:5-6 |
| 事业所规模5人以上 | 符号 T 与常用劳动者数、事业所规模5人以上一项并列。 | S14；estat_pdf_cumulative_symbols.txt:7-8 |
| 就业形态计 | 符号 0 与就業形态计一项并列。 | S14；estat_pdf_cumulative_symbols.txt:9-10 |
| 现金工资总额（名目） | 调查概要称名目工资（现金工资总额）；两份实数列头含现金工资总额字段。 | S1；estat_survey_00450071.txt:504；H1；estat_csvhead_cumulative_jissu.txt:2；H2；estat_csvhead_juurai_jissu.txt:2 |
| 月次 | 符号 01–12 对应 1–12 月；CY 对应年平均。 | S14；estat_pdf_cumulative_symbols.txt:3-70 |

月次行是否存在于完整当前版或従来版 CSV：NOT CAPTURED。API 维度代码、查询组合和某一行是否确实满足全部计划筛选：NOT CHECKED。

## 5. 修订、订正与基准更新通知

以下只概括捕获到的标题、日期和正文所述变化；未读取的链接附件不作概述。

| 材料 | 日期 | 捕获文字所述影响 | 证据 |
|---|---|---|---|
| 毎月勤労統計調査における「時系列比較のための推計値」等の公表について（30-1h） | 2020-08-11；2020-10-12、2021-08-24追加 | 厚生劳动省说明平成24年以后发布必要复元后的再集计值；平成16–23年制作时系列比较推计值，并在 e-Stat 发布相关实数/指数。 | S15；mhlw_maikin_30-1i.txt:40；S16；mhlw_maikin_30-1h.txt:2、35-46 |
| 毎月勤労統計調査における「従来の公表値」等の訂正について（30-1l） | 2020-11-25（日期来自历史通知目录；页面正文未列日期） | 对 e-Stat 所列统计表订正；列出給与阶级别事业所数与平成31年1月—令和2年8月従来公表值，并说明平成30年12月以前的季节调整值也有订正。 | S15；mhlw_maikin_30-1i.txt:50；S17；mhlw_maikin_30-1l.txt:2、36-40 |
| 毎月勤労統計調査における訂正等について（30-1o） | 2021-06-17；2021-06-25追加订正 | 页面称追加发布 2021年3月抽出调查系列；列出 2020 年奖金、抽出调查系列和従来公表值订正，并说明部分共通事业所数据也有订正。 | S18；mhlw_maikin_30-1o.txt:2、36-49 |
| 毎月勤労統計調査の変更について（30-1p） | 2021-10-01（日期来自历史通知目录） | 页面说明 令和4年1月分起东京都500人以上事业所全数调查的调查系統变更；特别调查的公表期日由调查年内改至次年1月底。 | S15；mhlw_maikin_30-1i.txt:38；S19；mhlw_maikin_30-1p.txt:35-45 |
| 令和4年1月基准时更新 | 2022-04-05通知；同月11日替换（首页所列） | 更新前令和3年12月以前的值以参考资料列出；更新说明将指数基准改为2020年平均=100。 | S15；mhlw_maikin_top.txt:56；S13；estat_pdf_pre2021_note.txt:3-14；S4；estat_files_zenkoku.txt:661-667 |
| 令和6年1月基准更新等影响说明 PDF | PDF 日期 2024-04-08；首页列有相关通知标题 | 令和6年1月確報纳入样本入替及季节调整值修订，并进行基准更新；指数从令和6年1月以后追溯修订。说明对工资、劳动时间等公布不含基准更新影响的伸び率，并给出相应参考值处理。 | S20；estat_pdf_benchmark_r6.txt:5-7、544-545、591-615、639-647；S15；mhlw_maikin_top.txt:48、50 |
| 毎月勤労統計：賃金データの見方 PDF | 2018年9月制作、同年12月更新、2021年5月修正 | PDF 说明平成16–29年的数值后来作机械性修正：平成24年以后用复元后的再集计值，平成23年以前用时系列比较推计值；这描述该资料的修正，不代表每张表均已逐项核验。 | S21；mhlw_pdf_mikata_20180927.txt:3-13；S15；mhlw_maikin_30-1i.txt:73 |
| 厚生劳动省首页当时所列的近期事项 | 2026-10-30页面捕获时：首页列出令和7年6月地方调查部分订正；另列2026年1月全国结果确报及样本入替影响通知。 | 首页只显示题名和日期；该处未捕获对应附件正文，故不补述其数据影响。 | S15；mhlw_maikin_top.txt:36-38 |

## 6. 范围限制与未捕获事项

- mhlw_maikin_30-1i.txt:72 只显示平成31年1月11日通知的标题。链接目标在本次捕获之外；不描述该通知正文。
- 完整 CSV/Excel 未捕获。只有当前版与従来版实数文件的前 12,288 字节头部；当前版与従来版指数・伸び率、赏与文件的下载 URL、完整字节数和列头均 NOT CAPTURED。
- 通知页链接的其他 PDF 未捕获；未用标题推断其内容。
- 没有捕获任何 API 响应，也未检查 API 查询或统计 Dashboard 结果。表4的 API statsDataId 和按发布时间取得历史发布的具体方法均未在本次来源中确认。
- e-Stat 当前数据库中是否另有可用于本计划的 statsDataId：NOT CHECKED。本文不作“不存在”的判断。
- mhlw_robots.txt 的捕获文本有一处路径形状被改写；本包不引用或复述该行。
