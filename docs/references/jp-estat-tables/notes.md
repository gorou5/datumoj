# e-Stat 选定统计表（jp-estat-tables）资料笔记

## 证据标签

引用只指向捕获正文行，不引用捕获文件第 1 行。每个直接引文都取自同一正文行，并按短语截取。`estat_dllist_*` 是从对应原 HTML 机械提取的索引；其中 statdisp_id 来自链接属性，不是页面可见文字。下文用到的表番号、表题和公开日期已与同组 `estat_dl_*` 的可见文字逐行核对。M1 是维护者计划摘录，只用于计划对照，不是官方来源。下表的“官方 URL”只取自相应捕获文件第 1 行的 URL 元数据，不作页面正文引文。

| 标签 | 材料 | 用途 | 官方 URL（捕获头部元数据） |
|---|---|---|---|
| S1 | e-Stat 人口推計 dbview；estat_dbview_0003443838.txt | 表题、分类、公开时间、维度和项目。  | https://www.e-stat.go.jp/dbview?sid=0003443838 |
| S2 | e-Stat 人口推計统计页；estat_statistics_00200524.txt | 政府统计名与代码。  | https://www.e-stat.go.jp/statistics/00200524 |
| S3 | e-Stat 人口推計月次数据列表可见文字；estat_dl_00200524_monthly_a.txt | 表番号、表题、调查年月、公开日期。  | https://www.e-stat.go.jp/stat-search/database?page=1&toukei=00200524&tstat=000000090001&cycle=1&tclass1=000001011678&tclass2=000001151566&layout=datalist&tclass3val=0 |
| S4 | 人口推計链接属性衍生索引；estat_dllist_00200524_monthly_a.txt | 同一表番号下的 statdisp_id 与公开日期；仅作衍生索引。  | 衍生索引；无独立捕获页 URL |
| S5 | e-Stat 労働力調査 dbview；estat_dbview_0003005865.txt | 表题、分类解释、公开时间、维度和项目。  | https://www.e-stat.go.jp/dbview?sid=0003005865 |
| S6 | e-Stat 労働力調査统计页；estat_statistics_00200531.txt | 政府统计名与代码。  | https://www.e-stat.go.jp/statistics/00200531 |
| S7 | e-Stat 労働力調査基本集計全国月次列表可见文字；estat_dl_00200531_zenkoku_monthly.txt | 所选表行及可见表题、日期。  | https://www.e-stat.go.jp/stat-search/database?page=1&toukei=00200531&tstat=000000110001&cycle=1&tclass1=000001040276&tclass2=000001040299&tclass3=000001040300&layout=datalist&tclass4val=0 |
| S8 | 労働力調査链接属性衍生索引；estat_dllist_00200531_zenkoku_monthly.txt | 113 行表题索引及字符串核对；仅作衍生索引。  | 衍生索引；无独立捕获页 URL |
| S9 | 総務省統計局 労働力調査页面；stat_roudou_2.txt | 页面出现的季節調整値文字。  | https://www.stat.go.jp/data/roudou/2.html |
| S10 | 総務省統計局 労働力調査月次结果页面；stat_roudou_tsuki.txt | 页面出现的季節調整値文字。  | https://www.stat.go.jp/data/roudou/sokuhou/tsuki/index.html |
| S11 | e-Stat 消費者物価指数 dbview；estat_dbview_0003427113.txt | 表题、分类、公开时间、维度和项目。  | https://www.e-stat.go.jp/dbview?sid=0003427113 |
| S12 | e-Stat 消費者物価指数统计页；estat_statistics_00200573.txt | 政府统计名与代码。  | https://www.e-stat.go.jp/statistics/00200573 |
| S13 | e-Stat 消費者物価指数数据库列表；estat_db_00200573.txt | 基准分类与列表日期观察。  | https://www.e-stat.go.jp/stat-search/database?page=1&toukei=00200573 |
| M1 | datumoj_plan_excerpt.md | 维护者计划主张；不是官方证据。 | 非官方来源；无独立捕获页 URL |

## 1. 人口推計：表1

| 页面字段 | 捕获文字与观察 | 证据 |
|---|---|---|
| 表题 | 「年齢（５歳階級），男女，月別人口－総人口（各月１日現在）-令和２年国勢調査基準-」；政府统计名「人口推計」；提供统计名「人口推計」。 | S1；estat_dbview_0003443838.txt:507；estat_dbview_0003443838.txt:510；estat_dbview_0003443838.txt:516 |
| 提供分类与表分类 | 提供分类1「各月1日現在人口」，提供分类2「概算値」，表分类「概算値」。 | S1；estat_dbview_0003443838.txt:517-522 |
| 公开时间 | 「2026-10-20 14:00」。 | S1；estat_dbview_0003443838.txt:523-524 |
| 政府统计代码 | 政府统计名「人口推計」；代码「00200524」。 | S2；estat_statistics_00200524.txt:500；estat_statistics_00200524.txt:502 |

dbview 可见的七个维度标题为 表章項目、人口、男女別、年齢5歳階級、概算値、全国、時間軸（年月日現在）（S1；estat_dbview_0003443838.txt:550-581）。计划相关项目在页面列为：人口「総人口」（S1；estat_dbview_0003443838.txt:669-672），性别「男女計」（S1；estat_dbview_0003443838.txt:673-676），年龄「総数」（S1；estat_dbview_0003443838.txt:677-679），地域「全国」（S1；estat_dbview_0003443838.txt:705-708）。这些是维度列表中的可见项目，不证明计划筛选已应用；页面列出 59 个时间项目，捕获范围从「2021年12月」到「2026年10月」（S1；estat_dbview_0003443838.txt:581-582；estat_dbview_0003443838.txt:710；estat_dbview_0003443838.txt:768）。计划的最近24个月窗口是否已选中为 NOT CHECKED。

人口推計列表的衍生索引在表番号 001 下列出新基准 statdisp_id「0003443838」和旧基准 statdisp_id「0003426414」，日期分别为「2026-10-20」与「2021-12-20」（S4；estat_dllist_00200524_monthly_a.txt:2-3）。可见页中当前基准行包含「令和２年国勢調査基準」，调查年月为「調査年月 -」，公开日期为「2026-10-20」（S3；estat_dl_00200524_monthly_a.txt:542；estat_dl_00200524_monthly_a.txt:543；estat_dl_00200524_monthly_a.txt:544）；旧基准行包含「平成27年国勢調査基準」，调查年月同为「調査年月 -」，公开日期为「2021-12-20」（S3；estat_dl_00200524_monthly_a.txt:548；estat_dl_00200524_monthly_a.txt:549；estat_dl_00200524_monthly_a.txt:550）。statdisp_id 来自链接属性，不能据此表述为页面正文。这个单次捕获只说明当时同一表番号有两个基准条目；下月是否沿用相同 sid 仍为 NOT CHECKED，需要后续捕获，且不外推至其他表。

与计划对照：计划标题与 dbview 表题一致；人口推計名称与政府统计代码 00200524 有页面依据。计划中的男女计、年龄总数、全国项目均出现在维度列表；最近24个月是否作为当前筛选条件显示不出来（M1；datumoj_plan_excerpt.md:10；见上述 S1、S2、S3）。

## 2. 労働力調査：表2

| 页面字段 | 捕获文字与观察 | 证据 |
|---|---|---|
| 表题 | 「労働力人口比率，就業率及び完全失業率（2000年1月～）」；政府统计名「労働力調査」；提供统计名「労働力調査」。 | S5；estat_dbview_0003005865.txt:507；estat_dbview_0003005865.txt:510；estat_dbview_0003005865.txt:517 |
| 提供分类 | 提供分类1「基本集計 全都道府県」；分类2「全国」；分类3「月次」。 | S5；estat_dbview_0003005865.txt:518-529 |
| 提供分类1解说 | 页面称时系列接续用数值会替换相应期间的数值：「時系列接続用数値に置き換えて掲載しています。」又称其他数据库数值记录「公表当時の結果数値を収録」。 | S5；estat_dbview_0003005865.txt:523；estat_dbview_0003005865.txt:525 |
| 表分类与公开时间 | `表分類` 标签后直接出现公开时间字段，捕获文字中未显示表分类值；公开时间为「2026-10-30 08:30」。 | S5；estat_dbview_0003005865.txt:530-532 |
| 政府统计代码 | 政府统计名「労働力調査」；代码「00200531」。 | S6；estat_statistics_00200531.txt:500-502 |

dbview 的六个维度标题为 表章項目、産業、就業状態、性別、地域、時間軸（月次）（S5；estat_dbview_0003005865.txt:558-584）。页面列出的项目包括 表章項目「率」（S5；estat_dbview_0003005865.txt:667-669）、产业「全産業」（S5；estat_dbview_0003005865.txt:670-672）、就业状态「労働力人口」「就業」「完全失業者」（S5；estat_dbview_0003005865.txt:672-675）、性别「総数」（S5；estat_dbview_0003005865.txt:676-679）、地域「全国」（S5；estat_dbview_0003005865.txt:680-681）和时间「2000年1月」（S5；estat_dbview_0003005865.txt:682-684）。维护者计划原标签为「男女計」（M1；datumoj_plan_excerpt.md:11），页面原标签为「総数」（S5；estat_dbview_0003005865.txt:677）；二者语义对应但标签不同，代码映射仍为 NOT CHECKED（M1；datumoj_plan_excerpt.md:18）。这些值列在页面中，不证明计划筛选已应用。

基本集計・全国・月次的可见列表中，所选表行显示表番号「1-1-5」、表题「労働力人口比率，就業率及び完全失業率（2000年1月～）」及公开日期「2026-10-30」（S7；estat_dl_00200531_zenkoku_monthly.txt:567-570）。衍生索引共 113 行标题；逐行核对其 113 个表番号、113 个表题、113 个公开日期，共 339 个字段，均在配套可见页面文字中匹配（S8；estat_dllist_00200531_zenkoku_monthly.txt:2-114；S7；estat_dl_00200531_zenkoku_monthly.txt:541-1218）。对这 113 个衍生标题查找 `季節調整` 未发现匹配；这是对衍生索引的字符串观察，不是页面对数据值性质的说明。

stat.go.jp 的労働力調査月次结果页面有「完全失業率（季節調整値）は2.4％。」（S10；stat_roudou_tsuki.txt:78）；另一页面以「未活用労働指標1（LU1）の季節調整値について」指向说明（S9；stat_roudou_2.txt:146）。这两处文字没有说明 dbview 表 0003005865 的数据究竟是原数值还是季调值。该表的数据性质为 NOT STATED；不以标题中是否出现季調字样或数值比较作推断。

与计划对照：表题、労働力調査名称及代码 00200531 一致；计划原标签「男女計」（M1；datumoj_plan_excerpt.md:11）与页面原标签「総数」（S5；estat_dbview_0003005865.txt:677）语义对应但标签不同；全国、2000年1月起可在维度或表题中找到。代码映射仍为 NOT CHECKED（M1；datumoj_plan_excerpt.md:18）。是否已应用这些筛选，以及本表数值是原数值还是季调值，分别为 NOT CHECKED 与 NOT STATED（M1；datumoj_plan_excerpt.md:11；datumoj_plan_excerpt.md:20；见 S5、S6、S7、S10）。

## 3. 消費者物価指数：表3

| 页面字段 | 捕获文字与观察 | 证据 |
|---|---|---|
| 表题 | 「消費者物価指数（2020年基準）」；政府统计名「消費者物価指数」；提供统计名「2020年基準消費者物価指数」。 | S11；estat_dbview_0003427113.txt:507；estat_dbview_0003427113.txt:510；estat_dbview_0003427113.txt:513 |
| 提供分类与表分类 | 捕获页未显示提供分类字段。`表分類` 标签后直接出现公开时间字段，未显示表分类值。 | S11；estat_dbview_0003427113.txt:512-516 |
| 公开时间 | 「2026-10-23 08:30」。 | S11；estat_dbview_0003427113.txt:515-516 |
| 政府统计代码 | 政府统计名「消費者物価指数」；代码「00200573」。 | S12；estat_statistics_00200573.txt:500-502 |

dbview 的四个维度标题为 表章項目、2020年基準品目、地域（2020年基準）、時間軸（年・月）（S11；estat_dbview_0003427113.txt:542-559）。计划相关值包含 表章項目「指数」（S11；estat_dbview_0003427113.txt:633-636）、品目「0001 総合」（S11；estat_dbview_0003427113.txt:639-641）以及地域「全国」（S11；estat_dbview_0003427113.txt:1430-1432）。时间维度标题含年、月；捕获列表中同时列有「2026年9月」和「2020年度」（S11；estat_dbview_0003427113.txt:1499；estat_dbview_0003427113.txt:1578）。月次筛选是否已应用为 NOT CHECKED。

品目列表中还明确列有五个季节调整项目：

- 「0901 総合（季節調整済）」（S11；estat_dbview_0003427113.txt:1425）
- 「0902 生鮮食品を除く総合（季節調整済）」（S11；estat_dbview_0003427113.txt:1426）
- 「0906 生鮮食品及びエネルギーを除く総合（季節調整済）」（S11；estat_dbview_0003427113.txt:1427）
- 「0921 財（季節調整済）」（S11；estat_dbview_0003427113.txt:1428）
- 「0924 サービス（季節調整済）」（S11；estat_dbview_0003427113.txt:1429）

捕获的全国项目只显示「全国」（S11；estat_dbview_0003427113.txt:1432），未显示对应地域代码。CPI dbview、统计页和数据库列表的正文中均未找到 00000；全国地域代码 00000 为 NOT STATED。维护者计划中写的 00000 不作为官方证据（M1；datumoj_plan_excerpt.md:12；datumoj_plan_excerpt.md:21；estat_statistics_00200573.txt:500-502；estat_db_00200573.txt:531-536）。

与计划对照：表题与页面相同；政府统计代码 00200573 已由统计页显示，因此计划中的该代码值获支持，但待核实状态已不再适用于本次捕获。指数、0001 总合、全国等值出现在维度项目中；月次筛选应用状态仍为 NOT CHECKED（M1；datumoj_plan_excerpt.md:12；datumoj_plan_excerpt.md:32；见 S11、S12）。表3 sid 未在相关页面正文行中显示，当前仅凭捕获元数据命名无法做正文级核验，记为 NOT CHECKED。

## 4. 总体限制

- 本包没有发起 API 请求，也没有使用 appId；getMetaInfo 维度代码为 NOT CHECKED。
- 页面数据单元格未捕获或核对；本笔记只记录捕获页字段和维度项目，不报告统计值。
- 所有页面观察来自 2026-10-30 的单次捕获，不能证明其后的更新或持续性。
- 表1 sid 的跨月行为、表2数据是原数值还是季调值、表3全国代码分别为 NOT CHECKED、NOT STATED、NOT STATED。
- 本包不判断许可、不建议选用某张表，也不把维护者计划当作官方结论。
